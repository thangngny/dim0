import { useEffect, type RefObject } from "react"
import { screenToWorld, type CanvasStore } from "@canvas-harness/core"
import { isTypingTarget } from "@/lib/dom/is-typing-target"
import { useHarnessAddImage } from "./use-add-image"


/**
 * Feature SP5 — paste an image from the clipboard onto the canvas.
 *
 * Drag-drop of image files already lands them as image nodes (see
 * `use-drop-files`). This hook adds the parallel paste path: Ctrl/Cmd+V
 * with an image on the clipboard (screenshots, copied images) uploads
 * the image + creates an image node at the viewport center.
 *
 * Ignores pastes that originate from a typing target (input / textarea /
 * contentEditable) so inline text editing keeps native paste behavior.
 * Only the FIRST image file is used — multi-image paste would stack
 * them on top of each other at the same center point.
 */
export const useHarnessPasteImage = (
  wrapRef: RefObject<HTMLElement | null>,
  store: CanvasStore,
  boardId: string | null,
  rootId: string | null,
): void => {
  const addImage = useHarnessAddImage(store, boardId, rootId)

  useEffect(() => {
    if (!boardId) return
    const onPaste = (event: ClipboardEvent): void => {
      // Don't hijack paste while editing text — native paste wins.
      if (isTypingTarget(event.target)) return
      const dt = event.clipboardData
      if (!dt) return
      const files = Array.from(dt.files ?? []).filter((f) => f.type.startsWith("image/"))
      if (files.length === 0) return
      // Some browsers expose the image only via items.
      const fromItems = Array.from(dt.items ?? [])
        .map((item) => (item.kind === "file" ? item.getAsFile() : null))
        .filter((f): f is File => !!f && f.type.startsWith("image/"))
      const image = files[0] ?? fromItems[0]
      if (!image) return

      event.preventDefault()
      const wrap = wrapRef.current
      const rect = wrap?.getBoundingClientRect()
      const center = rect
        ? { x: rect.width / 2, y: rect.height / 2 }
        : { x: 0, y: 0 }
      const world = screenToWorld(center, store.getCamera())
      void addImage(image, { position: world })
    }
    window.addEventListener("paste", onPaste)
    return () => window.removeEventListener("paste", onPaste)
  }, [wrapRef, store, boardId, rootId, addImage])
}