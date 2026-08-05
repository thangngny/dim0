// Cluster collapse/expand logic (feature D-rest), split out from the
// `ClusterOverlay` component so the React file only exports components
// (keeps Fast Refresh valid — see react-refresh/only-export-components).
//
// "Group & collapse selected" (context menu) assigns the selected nodes
// to a new canvas-harness Group and sets `hidden: true` on each member.
// `hidden` is a serialized Node field so it round-trips through collab.
// The overlay renders a proxy chip at the group's centroid while members
// are hidden; Expand sets `hidden: false` on every member so the cluster
// re-appears. Collapsed state persists per-board so it survives reload.
import {
  asGroupId,
  worldToScreen,
  type CameraState,
  type GroupId,
  type Node,
  type NodeId,
} from "@canvas-harness/core"
import { useCanvasStore } from "@canvas-harness/react"
import { removeCollapsedGroup, saveCollapsedGroup } from "./collapsed-groups"


/**
 * Create a group from the given nodes, assign membership, and collapse
 * (hide) the members. Returns the new group id. Called from the context
 * menu. Name defaults to "Cluster N" but can be overridden. Persists the
 * collapsed state per-board so it survives reload.
 */
export const groupAndCollapse = (
  store: ReturnType<typeof useCanvasStore>,
  nodes: Node[],
  boardId: string,
  name?: string,
): GroupId | null => {
  if (nodes.length < 2) return null
  const id = store.generateId() as unknown as GroupId
  const gid = asGroupId(String(id))
  const groupName = name?.trim() || `Cluster ${store.getAllGroups().length + 1}`
  store.upsertGroup({ id: gid, name: groupName })
  for (const n of nodes) {
    const groups = n.groups.includes(gid) ? n.groups : [...n.groups, gid]
    store.updateNode(n.id as NodeId, { groups, hidden: true })
  }
  saveCollapsedGroup(boardId, gid, groupName)
  return gid
}


/** Un-hide every member of a group (Expand) + drop it from the persisted set. */
export const expandGroup = (
  store: ReturnType<typeof useCanvasStore>,
  gid: GroupId,
  boardId: string,
): void => {
  const members = store.getAllNodes().filter((n) => n.groups.includes(gid))
  for (const n of members) {
    store.updateNode(n.id as NodeId, { hidden: false })
  }
  removeCollapsedGroup(boardId, gid)
}


/** Centroid (screen space) of a group's members, or null if none visible-in-store. */
export const groupCentroidScreen = (
  gid: GroupId,
  nodes: Node[],
  camera: CameraState,
): { x: number; y: number } | null => {
  const members = nodes.filter((n) => n.groups.includes(gid))
  if (members.length === 0) return null
  let cx = 0
  let cy = 0
  for (const n of members) {
    cx += n.x + n.w / 2
    cy += n.y + n.h / 2
  }
  cx /= members.length
  cy /= members.length
  return worldToScreen({ x: cx, y: cy }, camera)
}


/** Is every member of a group currently hidden (collapsed)? */
export const isGroupCollapsed = (gid: GroupId, nodes: Node[]): boolean => {
  const members = nodes.filter((n) => n.groups.includes(gid))
  if (members.length === 0) return false
  return members.every((n) => n.hidden === true)
}