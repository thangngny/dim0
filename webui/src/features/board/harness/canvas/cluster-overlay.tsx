import { useCallback, useState } from "react"
import { CaretDownIcon, SquaresFourIcon } from "@phosphor-icons/react"
import {
  asGroupId,
  worldToScreen,
  type CameraState,
  type Group,
  type GroupId,
  type Node,
  type NodeId,
} from "@canvas-harness/core"
import { useCamera, useCanvasStore, useNodes } from "@canvas-harness/react"
import { cn } from "@/lib/utils"
import { removeCollapsedGroup, saveCollapsedGroup } from "./collapsed-groups"
import { useBoardAppStore } from "../store/board-app-store"


/**
 * Feature D-rest — collapse/expand cluster.
 *
 * "Group & collapse selected" (context menu) assigns the selected nodes
 * to a new canvas-harness Group and sets `hidden: true` on each member.
 * `hidden` is a serialized Node field so it round-trips through collab.
 *
 * While a group's members are all hidden this overlay renders a proxy
 * chip at the group's centroid: the group name + a count + an "Expand"
 * button. Clicking Expand sets `hidden: false` on every member so the
 * cluster re-appears. The chip is pointer-events auto; the container is
 * pointer-events none so canvas gestures stay unobstructed.
 *
 * Hidden only when zoomed out (illegible) like the link overlay.
 */


/** Below this zoom the cluster proxy is hidden. */
const MIN_ZOOM_FOR_PROXY = 0.4


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
const groupCentroidScreen = (
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


type ProxyChipProps = {
  group: Group
  count: number
  screenX: number
  screenY: number
  onExpand: () => void
}


function ProxyChip({ group, count, screenX, screenY, onExpand }: ProxyChipProps) {
  return (
    <div
      className="pointer-events-auto absolute flex items-center gap-1.5 rounded-md border border-dashed border-primary/50 bg-background/95 px-2 py-1 text-xs shadow-sm backdrop-blur"
      style={{ left: screenX, top: screenY, transform: "translate(-50%, -50%)" }}
    >
      <SquaresFourIcon className="size-3.5 shrink-0 text-primary" weight="fill" />
      <span className="max-w-[160px] truncate font-medium text-foreground">
        {group.name || "Cluster"}
      </span>
      <span className="rounded-sm bg-muted px-1 text-[10px] tabular-nums text-muted-foreground">
        {count}
      </span>
      <button
        type="button"
        onClick={onExpand}
        className="flex items-center gap-0.5 rounded-sm border border-border bg-background px-1.5 py-0.5 text-[11px] font-medium hover:bg-muted"
      >
        <CaretDownIcon className="size-3" weight="bold" />
        Expand
      </button>
    </div>
  )
}


/** Mount inside `<Canvas children>`. Renders a proxy chip per collapsed group. */
export function ClusterOverlay() {
  const store = useCanvasStore()
  const nodes = useNodes()
  const camera = useCamera()
  const boardId = useBoardAppStore((s) => s.boardId)
  // Bump on expand so the chip list re-evaluates even if the change
  // event arrives slightly before useNodes re-renders.
  const [, setTick] = useState(0)

  const handleExpand = useCallback(
    (gid: GroupId) => {
      if (boardId) expandGroup(store, gid, boardId)
      setTick((t) => t + 1)
    },
    [store, boardId],
  )

  if (camera.z < MIN_ZOOM_FOR_PROXY) return null

  const groups = store.getAllGroups()
  const clusters = groups
    .map((g) => {
      if (!isGroupCollapsed(g.id, nodes)) return null
      const center = groupCentroidScreen(g.id, nodes, camera as CameraState)
      if (!center) return null
      // Cull offscreen.
      const vw = window.innerWidth
      const vh = window.innerHeight
      if (center.x < -200 || center.x > vw + 200 || center.y < -200 || center.y > vh + 200) {
        return null
      }
      const count = nodes.filter((n) => n.groups.includes(g.id)).length
      return (
        <ProxyChip
          key={String(g.id)}
          group={g}
          count={count}
          screenX={center.x}
          screenY={center.y}
          onExpand={() => handleExpand(g.id)}
        />
      )
    })
    .filter(Boolean)

  if (clusters.length === 0) return null

  return (
    <div className={cn("pointer-events-none absolute inset-0 z-30 overflow-hidden")}>
      {clusters}
    </div>
  )
}