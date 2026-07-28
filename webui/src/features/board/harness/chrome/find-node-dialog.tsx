import { useEffect, useMemo, useState } from "react"
import { MagnifyingGlassIcon } from "@phosphor-icons/react"
import type { Node, NodeId } from "@canvas-harness/core"
import { useCanvasStore } from "@canvas-harness/react"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { useDebouncedValue } from "@/features/board/hooks/use-debounce"
import { cn } from "@/lib/utils"
import { searchNodes, type NodeSearchResult } from "../canvas/find-nodes"
import { useHarnessWrapRef } from "../canvas/wrap-ref-context"


export type FindNodeDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
}


/** Highest zoom allowed when jumping to a found node. */
const MAX_FOCUS_ZOOM = 2


/**
 * "Find on board" dialog — fuzzy-locate a node by label or body text
 * and snap the viewport onto it. Opened via ⌘/Ctrl+F or the More menu.
 *
 * Results come from the pure `searchNodes` helper over the live store;
 * the query is debounced so a fast typist doesn't re-scan the whole
 * board per keystroke. Selecting a result centers the camera on the
 * node (reusing the same projection math as `useCenterFromUrl`) and
 * sets the canvas selection so the node is outlined.
 */
export function FindNodeDialog({ open, onOpenChange }: FindNodeDialogProps) {
  const store = useCanvasStore()
  const wrapRef = useHarnessWrapRef()
  const [query, setQuery] = useState("")
  const [activeIndex, setActiveIndex] = useState(0)
  const debouncedQuery = useDebouncedValue({ value: query, delay: 80 })

  // Reset the field + selection each time the dialog opens so a previous
  // query doesn't linger.
  useEffect(() => {
    if (open) {
      setQuery("")
      setActiveIndex(0)
    }
  }, [open])

  const results = useMemo<NodeSearchResult[]>(() => {
    if (!debouncedQuery.trim()) return []
    return searchNodes(store.getAllNodes(), debouncedQuery)
  }, [store, debouncedQuery])

  // Keep the active row in bounds as the result set shrinks.
  useEffect(() => {
    setActiveIndex((i) => Math.min(i, Math.max(0, results.length - 1)))
  }, [results.length])

  const focusNode = (id: string): void => {
    const node = store.getNode(id as NodeId)
    const wrap = wrapRef?.current
    if (!node || !wrap) return
    const rect = wrap.getBoundingClientRect()
    const cam = store.getCamera()
    const z = Math.min(MAX_FOCUS_ZOOM, Math.max(cam.z, 1))
    const center = { x: node.x + node.w / 2, y: node.y + node.h / 2 }
    store.setCamera({
      x: center.x - rect.width / (2 * z),
      y: center.y - rect.height / (2 * z),
      z,
    })
    store.setSelection([node.id as NodeId])
  }

  const pick = (id: string): void => {
    focusNode(id)
    onOpenChange(false)
  }

  // Keyboard nav inside the list: ↑/↓ move, Enter selects, Escape closes
  // (Radix handles Escape; we only need arrow + enter here).
  const onKeyDown = (e: React.KeyboardEvent): void => {
    if (e.key === "ArrowDown") {
      e.preventDefault()
      setActiveIndex((i) => Math.min(i + 1, results.length - 1))
      return
    }
    if (e.key === "ArrowUp") {
      e.preventDefault()
      setActiveIndex((i) => Math.max(i - 1, 0))
      return
    }
    if (e.key === "Enter" && results[activeIndex]) {
      e.preventDefault()
      pick(results[activeIndex].id)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[520px] gap-0 p-0">
        <DialogHeader className="sr-only">
          <DialogTitle>Find on board</DialogTitle>
        </DialogHeader>
        <div className="flex items-center gap-2 border-b border-border px-3 py-2">
          <MagnifyingGlassIcon className="size-4 shrink-0 text-muted-foreground" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Search nodes by title or content…"
            className="h-8 border-0 px-0 shadow-none focus-visible:ring-0"
            autoFocus
          />
        </div>
        <div className="max-h-[320px] overflow-y-auto py-1">
          {debouncedQuery.trim() && results.length === 0 ? (
            <div className="px-3 py-6 text-center text-sm text-muted-foreground">
              No matching nodes
            </div>
          ) : null}
          {results.map((r, i) => (
            <button
              key={r.id}
              type="button"
              onMouseEnter={() => setActiveIndex(i)}
              onClick={() => pick(r.id)}
              className={cn(
                "flex w-full flex-col items-start gap-0.5 px-3 py-2 text-left text-sm",
                i === activeIndex ? "bg-secondary" : "hover:bg-secondary/60",
              )}
            >
              <span className="line-clamp-1 font-medium text-foreground">
                {r.label}
              </span>
              {r.snippet && r.snippet !== r.label ? (
                <span className="line-clamp-1 font-mono text-xs text-muted-foreground">
                  {r.snippet}
                </span>
              ) : null}
            </button>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  )
}