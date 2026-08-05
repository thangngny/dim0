import { useCallback, useState } from "react"
import { CaretDownIcon, SquaresFourIcon } from "@phosphor-icons/react"
import { type CameraState, type Group, type GroupId } from "@canvas-harness/core"
import { useCamera, useCanvasStore, useNodes } from "@canvas-harness/react"
import { cn } from "@/lib/utils"
import { useBoardAppStore } from "../store/board-app-store"
import {
  expandGroup,
  groupCentroidScreen,
  isGroupCollapsed,
} from "./cluster-overlay-utils"


/**
 * Feature D-rest — collapse/expand cluster overlay.
 *
 * While a group's members are all hidden (collapsed via `groupAndCollapse`
 * from the context menu) this overlay renders a proxy chip at the group's
 * centroid: the group name + a count + an "Expand" button. Clicking Expand
 * un-hides every member so the cluster re-appears. The chip is
 * pointer-events auto; the container is pointer-events none so canvas
 * gestures stay unobstructed. Hidden only when zoomed out (illegible).
 *
 * Pure logic (`groupAndCollapse`/`expandGroup`/`isGroupCollapsed`/
 * `groupCentroidScreen`) lives in `cluster-overlay-utils.ts` so this file
 * only exports components — Fast Refresh requires that.
 */


/** Below this zoom the cluster proxy is hidden. */
const MIN_ZOOM_FOR_PROXY = 0.4


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