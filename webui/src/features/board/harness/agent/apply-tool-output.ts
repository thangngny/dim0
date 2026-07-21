import type { QueryClient } from "@tanstack/react-query"
import {
  asBatchId,
  type CanvasStore,
  type NodeId,
  type Op,
} from "@canvas-harness/core"
import { getBoardLink, getBoardNote } from "@/features/board/api/get-board"
import type {
  ChangeNoteKindOutput,
  CreateNoteOutput,
  DeleteSubtreeOutput,
  EditNoteOutput,
  LinkNotesOutput,
  MergeNotesOutput,
  RelayoutOutput,
  ReparentNoteOutput,
  SplitNoteOutput,
  WriteNoteOutput,
} from "@/features/agent/types/tool-outputs"
import type { Link } from "@/features/board/types/link"
import type { Note } from "@/features/board/types/note"
import { linkToEdge } from "../convert/link-to-edge"
import { noteToNode } from "../convert/note-to-node"


export type NoteToolOutput = CreateNoteOutput | WriteNoteOutput | EditNoteOutput


/** Union of the six structural tool outputs handled by this module. */
export type StructToolOutput =
  | ChangeNoteKindOutput
  | ReparentNoteOutput
  | DeleteSubtreeOutput
  | MergeNotesOutput
  | SplitNoteOutput
  | RelayoutOutput


/** Apply a list of ops to the store as a single `remote`-origin batch. */
const applyRemoteBatch = (store: CanvasStore, ops: Op[]): void => {
  if (ops.length === 0) return
  store.applyBatch({
    id: asBatchId(store.generateId()),
    clientId: store.clientId,
    ts: Date.now(),
    origin: "remote",
    ops,
  })
}


/** Result returned by `applyNoteOutput`. `null` when the output is for another board / skipped. */
export type ApplyNoteResult = {
  noteId: string
  created: boolean
  /** True when the note's parentId matches the current rootId — it's visible on the canvas. */
  onCanvas: boolean
}


/**
 * Apply a single agent note-tool output to the harness store.
 *
 * Fetches the canonical Note from the server, converts to a Node,
 * then either adds or updates the corresponding node via a
 * `remote`-origin op batch. Remote batches bypass our debounced save
 * loop (the agent already wrote to the server) and the undo stack.
 *
 * If the note belongs to a different scope than the current `rootId`
 * (e.g. it's nested in another folder), we **don't** materialize a
 * phantom node on the current canvas — we just push the fresh Note
 * into the React Query cache so any open subpage panel reflects the
 * edit. The `onCanvas: false` return value signals that to the caller.
 */
export const applyNoteOutput = async (
  store: CanvasStore,
  queryClient: QueryClient,
  activeBoardId: string,
  rootId: string | null,
  output: NoteToolOutput,
): Promise<ApplyNoteResult | null> => {
  if (output.graphUid !== activeBoardId || !output.noteId) return null

  const isNewlyCreated =
    output.type === "create_note" ||
    (output.type === "write_note" && output.action === "created")

  let note: Note
  try {
    note = await getBoardNote(activeBoardId, output.noteId)
  } catch (err) {
    console.error("[harness/agent] failed to fetch note", output.noteId, err)
    return null
  }

  // Keep the per-note query cache fresh so an open subpage panel sees
  // the edit even when the note isn't on the canvas.
  queryClient.setQueryData(["note", activeBoardId, output.noteId], note)

  const noteParentId = note.parentId ?? undefined
  const currentRootId = rootId ?? undefined
  const onCanvas = noteParentId === currentRootId

  if (!onCanvas) {
    return { noteId: output.noteId, created: isNewlyCreated, onCanvas: false }
  }

  const node = noteToNode(note)
  const existing = store.getNode(node.id as NodeId)
  const op: Op = existing
    ? { type: "node.update", id: node.id, patch: node, prev: existing }
    : { type: "node.add", node }
  applyRemoteBatch(store, [op])

  return { noteId: output.noteId, created: isNewlyCreated, onCanvas: true }
}


/**
 * Apply a single agent link-tool output to the harness store. Mirrors
 * `applyNoteOutput` but for edges. Returns the resolved link id or
 * null when the output is for a different board / missing data.
 */
export const applyLinkOutput = async (
  store: CanvasStore,
  activeBoardId: string,
  output: LinkNotesOutput,
): Promise<string | null> => {
  if (output.graphUid !== activeBoardId || !output.linkId) return null

  let link: Link
  try {
    link = await getBoardLink(activeBoardId, output.linkId)
  } catch (err) {
    console.error("[harness/agent] failed to fetch link", output.linkId, err)
    return null
  }

  // Build the node lookup the convert layer needs for endpoint
  // resolution. Use live store state so the link picks up wherever
  // its attached nodes are right now.
  const nodes = new Map(store.getAllNodes().map((n) => [n.id as unknown as string, n]))
  const edge = linkToEdge(link, nodes)
  const existing = store.getEdge(edge.id)
  const op: Op = existing
    ? { type: "edge.update", id: edge.id, patch: edge, prev: existing }
    : { type: "edge.add", edge }
  applyRemoteBatch(store, [op])

  return output.linkId
}


/**
 * Apply a `change_note_kind` structural output. The mutation is broadcast
 * to peers via the collab WS; for the acting client we re-fetch the fresh
 * note and apply a `node.update` so the canvas reflects the new kind
 * without waiting for the round-trip. Returns null when skipped.
 */
export const applyChangeKindOutput = async (
  store: CanvasStore,
  queryClient: QueryClient,
  activeBoardId: string,
  rootId: string | null,
  output: ChangeNoteKindOutput,
): Promise<ApplyNoteResult | null> => {
  if (output.graphUid !== activeBoardId || !output.noteId) return null
  return applyNoteOutput(store, queryClient, activeBoardId, rootId, {
    type: "write_note",
    action: "rewritten",
    noteId: output.noteId,
    graphUid: output.graphUid,
    label: null,
    noteType: "rectangle",
    parentId: null,
  } as WriteNoteOutput)
}


/**
 * Apply a `reparent_note` structural output. Re-fetches the fresh note
 * (its parentId changed) and applies a `node.update` / `node.add` /
 * `node.remove` via `applyNoteOutput` depending on whether the note is
 * now visible on the current canvas. Peers receive the move via WS.
 */
export const applyReparentNoteOutput = async (
  store: CanvasStore,
  queryClient: QueryClient,
  activeBoardId: string,
  rootId: string | null,
  output: ReparentNoteOutput,
): Promise<ApplyNoteResult | null> => {
  if (output.graphUid !== activeBoardId || !output.noteId) return null
  return applyNoteOutput(store, queryClient, activeBoardId, rootId, {
    type: "write_note",
    action: "rewritten",
    noteId: output.noteId,
    graphUid: output.graphUid,
    label: null,
    noteType: "rectangle",
    parentId: output.parentId,
  } as WriteNoteOutput)
}


/**
 * Apply a `delete_subtree` structural output. The output carries only
 * counts (not ids), so the actual node/edge removal is delivered to all
 * clients — including the acting one — via the collab WS path. This
 * function is a no-op that signals "handled by WS".
 */
export const applyDeleteSubtreeOutput = async (
  _store: CanvasStore,
  _queryClient: QueryClient,
  _activeBoardId: string,
  _rootId: string | null,
  output: DeleteSubtreeOutput,
): Promise<ApplyNoteResult | null> => {
  if (output.graphUid !== _activeBoardId) return null
  return null
}


/**
 * Apply a `merge_notes` structural output. The surviving target note is
 * re-fetched and updated on the canvas; the absorbed notes are removed by
 * the collab WS path. Returns null when skipped.
 */
export const applyMergeNotesOutput = async (
  store: CanvasStore,
  queryClient: QueryClient,
  activeBoardId: string,
  rootId: string | null,
  output: MergeNotesOutput,
): Promise<ApplyNoteResult | null> => {
  if (output.graphUid !== activeBoardId || !output.targetId) return null
  return applyNoteOutput(store, queryClient, activeBoardId, rootId, {
    type: "write_note",
    action: "rewritten",
    noteId: output.targetId,
    graphUid: output.graphUid,
    label: null,
    noteType: "rectangle",
    parentId: null,
  } as WriteNoteOutput)
}


/**
 * Apply a `split_note` structural output. Each newly created note is
 * fetched and added to the canvas via `applyNoteOutput`. When the
 * original note was deleted, the collab WS path delivers its removal.
 * Returns the last created note's result (or null when skipped).
 */
export const applySplitNoteOutput = async (
  store: CanvasStore,
  queryClient: QueryClient,
  activeBoardId: string,
  rootId: string | null,
  output: SplitNoteOutput,
): Promise<ApplyNoteResult | null> => {
  if (output.graphUid !== activeBoardId || output.createdIds.length === 0) return null
  let last: ApplyNoteResult | null = null
  for (const createdId of output.createdIds) {
    const result = await applyNoteOutput(store, queryClient, activeBoardId, rootId, {
      type: "create_note",
      noteId: createdId,
      graphUid: output.graphUid,
      label: null,
      noteType: "rectangle",
      parentId: null,
    } as CreateNoteOutput)
    if (result) last = result
  }
  return last
}


/**
 * Apply a `relayout_board` structural output. The new positions are
 * broadcast to all clients via the collab WS path; this function is a
 * no-op that signals "handled by WS". Returns null when the output is
 * for a different board.
 */
export const applyRelayoutOutput = async (
  _store: CanvasStore,
  _queryClient: QueryClient,
  _activeBoardId: string,
  _rootId: string | null,
  output: RelayoutOutput,
): Promise<ApplyNoteResult | null> => {
  if (output.graphUid !== _activeBoardId) return null
  return null
}
