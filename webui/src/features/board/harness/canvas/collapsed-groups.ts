import type { CanvasStore } from "@canvas-harness/core"
import { asGroupId, type GroupId, type NodeId } from "@canvas-harness/core"


/**
 * Per-board persistence for collapsed clusters (feature D-rest).
 *
 * `node.hidden` is a canvas-harness Node field that syncs between peers
 * in real time but does NOT round-trip through the Dim0 Note model → DB
 * (the Note schema has no `hidden` property). So a collapsed cluster
 * re-appears on reload. To make collapse persist without a cross-stack
 * schema change, we record the collapsed group ids + their names per
 * board in localStorage and re-apply `hidden` (and re-upsert the Group
 * metadata, which also isn't persisted) after the board hydrates.
 *
 * Membership itself (node.groups / style.groupIds) IS persisted on each
 * note, so on reload the members still carry the group id — we only need
 * to flip their `hidden` flag back on + recreate the Group name.
 */


type CollapsedEntry = { name: string }


const storageKey = (boardId: string): string => `dim0_collapsed_groups:${boardId}`


/** Read the persisted collapsed-group map for a board. */
const readMap = (boardId: string): Record<string, CollapsedEntry> => {
  try {
    const raw = localStorage.getItem(storageKey(boardId))
    if (!raw) return {}
    return JSON.parse(raw) as Record<string, CollapsedEntry>
  } catch {
    return {}
  }
}


/** Write the full collapsed-group map for a board. */
const writeMap = (boardId: string, map: Record<string, CollapsedEntry>): void => {
  try {
    localStorage.setItem(storageKey(boardId), JSON.stringify(map))
  } catch {
    // ignore quota / private-mode errors.
  }
}


/** Record that a group is collapsed (called from groupAndCollapse). */
export const saveCollapsedGroup = (boardId: string, gid: GroupId, name: string): void => {
  const map = readMap(boardId)
  map[String(gid)] = { name }
  writeMap(boardId, map)
}


/** Remove a group from the collapsed set (called from expandGroup). */
export const removeCollapsedGroup = (boardId: string, gid: GroupId): void => {
  const map = readMap(boardId)
  delete map[String(gid)]
  writeMap(boardId, map)
}


/**
 * After a board hydrates, re-apply collapse: for each persisted
 * collapsed group, recreate the Group metadata (name) and hide every
 * member node. No-op when nothing is stored. Safe to call repeatedly.
 */
export const restoreCollapsedGroups = (store: CanvasStore, boardId: string): void => {
  const map = readMap(boardId)
  const ids = Object.keys(map)
  if (ids.length === 0) return
  const nodes = store.getAllNodes()
  for (const id of ids) {
    const gid = asGroupId(id)
    store.upsertGroup({ id: gid, name: map[id].name })
    const members = nodes.filter((n) => n.groups.includes(gid))
    for (const m of members) {
      store.updateNode(m.id as NodeId, { hidden: true })
    }
  }
}