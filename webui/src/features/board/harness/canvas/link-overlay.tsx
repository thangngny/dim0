import { useCallback, useSyncExternalStore } from "react"
import { ArrowUpRightIcon, CheckIcon } from "@phosphor-icons/react"
import { worldToScreen, type CameraState, type Node, type NodeId } from "@canvas-harness/core"
import { useCamera, useNodes } from "@canvas-harness/react"
import { extractLinks } from "./extract-links"


/**
 * Feature E — clickable-link + source checklist overlay.
 *
 * Research nodes render their body text as a bitmap (canvas-harness draws
 * inline text), so URLs inside the body aren't natively clickable. This
 * overlay sits inside the canvas absolute container (via `<Canvas
 * children>`), reads every node's `content`, and for nodes that cite
 * sources renders a small "Sources" checklist pinned under the node:
 *
 *   [✓] Behance case ↗
 *   [ ] https://youtu.be/abc ↗
 *
 * The label opens the URL in a new tab; the checkbox toggles a
 * "reviewed" mark persisted to localStorage so the user can track which
 * references they've already opened. The container is pointer-events
 * none so it never blocks canvas gestures — only the chips intercept.
 *
 * Rendered only above a zoom threshold (links are illegible when zoomed
 * out) and culled to the viewport so panning a large board stays cheap.
 */


/** Below this zoom the checklist is hidden (text illegible). */
const MIN_ZOOM_FOR_LINKS = 0.5
/** Vertical gap between the node's bottom edge and the checklist. */
const CHIP_GAP_PX = 6
/** Max nodes rendered per frame (research boards are small; safety net). */
const MAX_LINK_NODES = 40


// ---- reviewed-state store (module singleton, localStorage-backed) ------------

type ReviewedKey = string // `${nodeId}::${url}`


const reviewed = new Set<ReviewedKey>()
const reviewedListeners = new Set<() => void>()
let reviewedLoaded = false


const loadReviewed = (): void => {
  if (reviewedLoaded) return
  reviewedLoaded = true
  try {
    const raw = localStorage.getItem("dim0_link_reviewed")
    if (raw) for (const k of JSON.parse(raw) as string[]) reviewed.add(k)
  } catch {
    // ignore — corrupt storage just starts fresh.
  }
}


const persistReviewed = (): void => {
  try {
    localStorage.setItem("dim0_link_reviewed", JSON.stringify([...reviewed]))
  } catch {
    // ignore quota / private-mode errors.
  }
}


const reviewedKey = (nodeId: NodeId, url: string): ReviewedKey => `${String(nodeId)}::${url}`


const toggleReviewed = (nodeId: NodeId, url: string): void => {
  const k = reviewedKey(nodeId, url)
  if (reviewed.has(k)) reviewed.delete(k)
  else reviewed.add(k)
  persistReviewed()
  for (const cb of reviewedListeners) cb()
}


const subscribeReviewed = (cb: () => void): (() => void) => {
  reviewedListeners.add(cb)
  return () => {
    reviewedListeners.delete(cb)
  }
}


/** Hook: the set of reviewed keys + a toggler. Re-renders on toggle. */
const useReviewedLinks = (): {
  isReviewed: (nodeId: NodeId, url: string) => boolean
  toggle: (nodeId: NodeId, url: string) => void
} => {
  useSyncExternalStore(subscribeReviewed, () => reviewed.size, () => 0)
  loadReviewed()
  return {
    isReviewed: (nodeId, url) => reviewed.has(reviewedKey(nodeId, url)),
    toggle: toggleReviewed,
  }
}


// ---- overlay component --------------------------------------------------------


type LinkClusterProps = {
  node: Node
  screenX: number
  screenY: number
  isReviewed: (nodeId: NodeId, url: string) => boolean
  toggle: (nodeId: NodeId, url: string) => void
}


/** One node's "Sources" checklist, absolutely positioned under the node. */
function LinkCluster({ node, screenX, screenY, isReviewed, toggle }: LinkClusterProps) {
  const links = extractLinks(typeof node.content === "string" ? node.content : "")
  if (links.length === 0) return null
  return (
    <div
      className="pointer-events-auto absolute flex max-w-[280px] flex-col gap-0.5 rounded-md border border-border/60 bg-background/95 px-1.5 py-1 text-[11px] shadow-sm backdrop-blur"
      style={{ left: screenX, top: screenY }}
    >
      <div className="px-0.5 text-[9px] font-semibold uppercase tracking-wide text-muted-foreground">
        Sources
      </div>
      {links.map((l) => {
        const done = isReviewed(node.id as NodeId, l.url)
        return (
          <div key={l.url} className="flex items-center gap-1">
            <button
              type="button"
              aria-label={done ? "Mark source not reviewed" : "Mark source reviewed"}
              aria-pressed={done}
              onClick={() => toggle(node.id as NodeId, l.url)}
              className={`flex size-3.5 shrink-0 items-center justify-center rounded-[3px] border ${
                done
                  ? "border-emerald-500 bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
                  : "border-border text-transparent hover:border-foreground/40"
              }`}
            >
              <CheckIcon className="size-2.5" weight="bold" />
            </button>
            <a
              href={l.url}
              target="_blank"
              rel="noopener noreferrer"
              className={`flex min-w-0 items-center gap-0.5 truncate hover:underline ${
                done ? "text-muted-foreground line-through" : "text-foreground"
              }`}
            >
              <span className="truncate">{l.label}</span>
              <ArrowUpRightIcon className="size-2.5 shrink-0 opacity-60" />
            </a>
          </div>
        )
      })}
    </div>
  )
}


/**
 * Mount inside `<Canvas children>`. Reads all nodes + the live camera,
 * renders a Sources checklist under every node that cites links.
 */
export function LinkOverlay() {
  const nodes = useNodes()
  const camera = useCamera()
  const { isReviewed, toggle } = useReviewedLinks()

  // Viewport size for culling. The canvas container is the overlay's
  // offset parent; window inner size is a safe generous bound (culling
  // is an optimization, not a correctness concern).
  const vw = window.innerWidth
  const vh = window.innerHeight

  const renderCluster = useCallback(
    (node: Node) => {
      const bottomLeft = worldToScreen(
        { x: node.x, y: node.y + node.h },
        camera as CameraState,
      )
      // Cull nodes whose bottom edge is far outside the viewport.
      if (bottomLeft.x < -300 || bottomLeft.x > vw + 300) return null
      if (bottomLeft.y < -200 || bottomLeft.y > vh + 200) return null
      return (
        <LinkCluster
          key={String(node.id)}
          node={node}
          screenX={bottomLeft.x}
          screenY={bottomLeft.y + CHIP_GAP_PX}
          isReviewed={isReviewed}
          toggle={toggle}
        />
      )
    },
    [camera, vw, vh, isReviewed, toggle],
  )

  // Hide entirely when zoomed out — links are illegible and the
  // per-node DOM would clutter the overview.
  if (camera.z < MIN_ZOOM_FOR_LINKS) return null

  // Only consider visible nodes that could carry links; cap the count.
  // Hidden nodes (collapsed cluster members) are skipped so their
  // checklist doesn't float over an empty spot.
  const clusters = nodes
    .filter(
      (n) =>
        !n.hidden &&
        typeof n.content === "string" &&
        n.content.includes("http"),
    )
    .slice(0, MAX_LINK_NODES)
    .map(renderCluster)
    .filter(Boolean)

  if (clusters.length === 0) return null

  return (
    <div className="pointer-events-none absolute inset-0 z-30 overflow-hidden">
      {clusters}
    </div>
  )
}