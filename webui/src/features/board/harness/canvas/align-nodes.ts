import type { Node } from "@canvas-harness/core"


/**
 * Pure alignment math for the canvas "Align" actions. Kept out of the
 * context menu so the geometry is unit-testable without the store.
 *
 * Each mode returns the minimal position patch per node id — only the
 * moved axis is included so a vertical align doesn't clobber a node's
 * x. Callers apply the patches via `store.updateNode(id, patch)`.
 */


export type AlignMode =
  | "left"
  | "right"
  | "center-h"
  | "top"
  | "bottom"
  | "middle-v"
  | "distribute-h"
  | "distribute-v"


export type PositionPatch = { x?: number; y?: number }


/** Minimum node count that makes sense for each mode. */
export const minNodesFor = (mode: AlignMode): number =>
  mode.startsWith("distribute") ? 3 : 2


/**
 * Compute new positions for the given nodes under an alignment mode.
 * Returns a Map<id, PositionPatch>. Modes that need ≥2 nodes return an
 * empty map when given fewer (the caller should also guard the UI).
 *
 * Alignment is relative to the selection's own bounding box — align
 * "left" snaps every node's left edge to the selection's min left
 * edge, etc. Distribute spaces nodes evenly between the extremes.
 */
export const alignNodes = (
  nodes: Node[],
  mode: AlignMode,
): Map<string, PositionPatch> => {
  const out = new Map<string, PositionPatch>()
  if (nodes.length < minNodesFor(mode)) return out

  const xs = nodes.map((n) => n.x)
  const rights = nodes.map((n) => n.x + n.w)
  const ys = nodes.map((n) => n.y)
  const bottoms = nodes.map((n) => n.y + n.h)
  const minX = Math.min(...xs)
  const maxX = Math.max(...rights)
  const minY = Math.min(...ys)
  const maxY = Math.max(...bottoms)
  const bboxCenterX = (minX + maxX) / 2
  const bboxCenterY = (minY + maxY) / 2

  switch (mode) {
    case "left":
      for (const n of nodes) out.set(String(n.id), { x: minX })
      return out
    case "right":
      for (const n of nodes) out.set(String(n.id), { x: maxX - n.w })
      return out
    case "center-h":
      for (const n of nodes) out.set(String(n.id), { x: bboxCenterX - n.w / 2 })
      return out
    case "top":
      for (const n of nodes) out.set(String(n.id), { y: minY })
      return out
    case "bottom":
      for (const n of nodes) out.set(String(n.id), { y: maxY - n.h })
      return out
    case "middle-v":
      for (const n of nodes) out.set(String(n.id), { y: bboxCenterY - n.h / 2 })
      return out
    case "distribute-h": {
      // Sort by left edge; space left edges evenly between the first
      // and last node's left edge. Gaps stay positive because equal
      // spacing of left edges across a wider span than the nodes sum
      // to requires ≥3 nodes (guarded above).
      const sorted = [...nodes].sort((a, b) => a.x - b.x)
      const first = sorted[0].x
      const last = sorted[sorted.length - 1].x
      const step = sorted.length > 1 ? (last - first) / (sorted.length - 1) : 0
      sorted.forEach((n, i) => out.set(String(n.id), { x: first + step * i }))
      return out
    }
    case "distribute-v": {
      const sorted = [...nodes].sort((a, b) => a.y - b.y)
      const first = sorted[0].y
      const last = sorted[sorted.length - 1].y
      const step = sorted.length > 1 ? (last - first) / (sorted.length - 1) : 0
      sorted.forEach((n, i) => out.set(String(n.id), { y: first + step * i }))
      return out
    }
  }
}