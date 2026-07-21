import { useSyncExternalStore } from "react"
import type { NodeId } from "@canvas-harness/core"
import { useChatStore } from "../store/chat-store"
import { useBoardAppStore } from "@/features/board/harness/store/board-app-store"
import { getCanvasStoreRef } from "@/features/board/harness/canvas-store-ref"
import { nodeToNote } from "@/features/board/harness/convert/node-to-note"
import { buildContextTextFromNodes } from "@/features/board/utils/context-text"
import { queryClient } from "@/query-client"
import type { Note } from "@/features/board/types/note"
import type { NoteNode } from "@/features/board/types/flow"


const MAX_MESSAGE_CONTEXT_CHARS = 12000


/**
 * Subscribe to the harness selection without depending on
 * `<CanvasProvider>` being an ancestor. We use the module-level
 * `getCanvasStoreRef` bridge because the floating-island composer
 * lives as a sibling of `<HarnessCanvas>` (and thus its
 * `CanvasProvider`), so `useSelection()` from `@canvas-harness/react`
 * isn't reachable here.
 *
 * Returns `0` when no store is mounted (login screen / dashboard).
 * Re-fires only when the lib's `'selection'` event channel emits —
 * which excludes pan/zoom/drag/hover.
 */
const useHarnessSelectionLength = (): number =>
  useSyncExternalStore(
    (cb) => {
      const store = getCanvasStoreRef()
      if (!store) return () => undefined
      return store.subscribe("selection", cb)
    },
    () => getCanvasStoreRef()?.getSelection().length ?? 0,
    () => 0,
  )


/** Selection id fingerprint so chip title updates when the set changes. */
const useHarnessSelectionKey = (): string =>
  useSyncExternalStore(
    (cb) => {
      const store = getCanvasStoreRef()
      if (!store) return () => undefined
      return store.subscribe("selection", cb)
    },
    () => (getCanvasStoreRef()?.getSelection() ?? []).join(","),
    () => "",
  )


/**
 * Synchronously read a note from wherever it lives:
 *  - the active canvas-harness scene (covers on-canvas notes)
 *  - the React Query `["note", boardId, noteId]` cache (sub-pages
 *    reached via the editor's `/subpage` flow that aren't on the
 *    current canvas scope)
 */
const readNote = (noteId: string): Note | undefined => {
  const store = getCanvasStoreRef()
  if (store) {
    const node = store.getNode(noteId as NodeId)
    if (node) return nodeToNote(node)
  }
  const boardId = useBoardAppStore.getState().boardId
  if (!boardId) return undefined
  return queryClient.getQueryData<Note>(["note", boardId, noteId])
}


/**
 * Reactive boolean — `true` when there's anything to attach to the
 * next message. Composer UIs use this to render the "selection
 * attached" chip. Re-renders only on selection / active-surface
 * changes; pan / zoom / drag don't trigger it (canvas-harness's
 * `'selection'` channel fires only on selection set changes).
 */
export const useHasMessageContext = (
  { enabled = true }: { enabled?: boolean } = {},
): boolean => {
  const enableMessageBoardContextSelection = useChatStore(
    (state) => state.enableMessageBoardContextSelection,
  )
  // Subscribe to canvas-harness's `'selection'` channel via the
  // module-level bridge (no `<CanvasProvider>` ancestor required —
  // the floating-island composer lives as a sibling of HarnessCanvas).
  const selectionLength = useHarnessSelectionLength()
  const hasSelection = enabled && selectionLength > 0
  // Active page is *always* sent as context when a surface is open,
  // regardless of the selection toggle — the indicator reflects that.
  const hasActiveSurface = useBoardAppStore(
    (state) => Boolean(state.activeNodeSurface),
  )
  return (
    enabled &&
    (hasActiveSurface || (enableMessageBoardContextSelection && hasSelection))
  )
}


export type SelectionContextSummary = {
  count: number
  /** Short label for the first selected note (truncated). */
  title: string
  /** First selected node id when count > 0. */
  primaryId?: string
}


/**
 * Reactive summary of the current canvas selection for chip UI
 * (`@selection (n): title`). Re-subscribes on selection channel only.
 */
export const useSelectionContextSummary = (
  { enabled = true }: { enabled?: boolean } = {},
): SelectionContextSummary => {
  const enableMessageBoardContextSelection = useChatStore(
    (state) => state.enableMessageBoardContextSelection,
  )
  const selectionKey = useHarnessSelectionKey()
  const ids = selectionKey ? selectionKey.split(",").filter(Boolean) : []
  const count =
    enabled && enableMessageBoardContextSelection ? ids.length : 0

  if (count === 0) {
    return { count: 0, title: "" }
  }

  const store = getCanvasStoreRef()
  if (!store) return { count, title: "" }

  const firstId = ids[0]
  if (!firstId) return { count, title: "" }

  const node = store.getNode(firstId as NodeId)
  if (!node) return { count, title: "", primaryId: firstId }

  const note = nodeToNote(node)
  const label = (note.label?.markdown ?? "").trim()
  const content = (note.content?.markdown ?? "").trim()
  const raw = label || content.split("\n")[0] || firstId
  const title = raw.length > 36 ? `${raw.slice(0, 36)}…` : raw
  return { count, title, primaryId: firstId }
}


/**
 * One-shot lazy builder for the per-message board context. Resolves
 * the active surface (if any) or otherwise the current canvas
 * selection, converts canvas-harness Nodes back to Note shapes, and
 * renders the structured `<SelectedNote>` blocks via
 * `buildContextTextFromNodes`. Truncated to `MAX_MESSAGE_CONTEXT_CHARS`.
 *
 * Call from a submit handler — never inside a render — so the
 * conversion + text build only happens when the user actually presses
 * send.
 */
export const buildMessageContext = (
  { enabled = true }: { enabled?: boolean } = {},
): string | undefined => {
  if (!enabled) return undefined

  // Active surface wins. The same note would otherwise appear via the
  // canvas selection too, which would duplicate it; sending it once
  // through the `<SelectedNote>` block keeps the format coherent.
  const activeSurface = useBoardAppStore.getState().activeNodeSurface
  if (activeSurface) {
    const note = readNote(activeSurface.nodeId)
    if (!note) return undefined
    const synthetic = { id: note.id, data: note } as unknown as NoteNode
    const block = buildContextTextFromNodes([synthetic]).trim()
    if (!block) return undefined
    return block.slice(0, MAX_MESSAGE_CONTEXT_CHARS)
  }

  const enableMessageBoardContextSelection =
    useChatStore.getState().enableMessageBoardContextSelection
  if (!enableMessageBoardContextSelection) return undefined

  const store = getCanvasStoreRef()
  if (!store) return undefined
  const selectionIds = store.getSelection()
  if (selectionIds.length === 0) return undefined

  const syntheticNodes: NoteNode[] = []
  for (const id of selectionIds) {
    const node = store.getNode(id as NodeId)
    if (!node) continue
    const note = nodeToNote(node)
    syntheticNodes.push({ id: note.id, data: note } as unknown as NoteNode)
  }
  if (syntheticNodes.length === 0) return undefined

  const text = buildContextTextFromNodes(syntheticNodes).trim()
  if (!text) return undefined
  return text.slice(0, MAX_MESSAGE_CONTEXT_CHARS)
}
