import { useEffect } from "react"
import type { CanvasStore } from "@canvas-harness/core"
import { isTypingTarget } from "@/lib/dom/is-typing-target"
import { useBoardAppStore } from "../store/board-app-store"


/**
 * Whether the event originated from a focused button-like control.
 * Space "clicks" a focused button by default, which collides with
 * hold-Space-to-pan: after clicking e.g. the sidebar trigger, every
 * Space press meant for panning would re-fire that button. We suppress
 * Space's default activation only for these targets.
 */
const isButtonTarget = (target: EventTarget | null): boolean => {
  if (!(target instanceof HTMLElement)) return false
  return target.closest("button, [role='button']") !== null
}


/** Single-key shortcuts that map straight to a tool-mode swap. */
const TOOL_SHORTCUTS: Record<string, string> = {
  p: "pan",
  h: "pan",
  v: "select",
  a: "arrow",
  t: "text",
  n: "sheet",
  r: "rect",
  o: "ellipse",
  d: "diamond",
  y: "code-sandbox",
}


/**
 * Global keyboard bindings for the canvas-harness board. Mirrors
 * prod's `use-board-shortcuts` keymap so muscle memory carries over:
 *
 *  - Cmd/Ctrl+Z / Shift+Z / Cmd+Y → undo / redo
 *  - P / H                        → pan tool
 *  - V                            → select tool
 *  - A                            → connector (arrow)
 *  - T                            → text tool
 *  - N                            → sheet tool
 *  - R / O / D                    → rect / ellipse / diamond tool
 *  - Y                            → code-sandbox tool
 *  - S                            → open Shapes menu
 *  - M                            → toggle Slides panel
 *  - G                            → open Icons search dialog
 *  - I                            → open Images search dialog
 *
 * Skipped when focus is in an input / textarea / contentEditable so
 * inline editing keeps the native shortcuts. canvas-harness already
 * wires Cmd+C/X/V/[]/]} internally — see Canvas.tsx.
 */
export const useBoardKeyboard = (store: CanvasStore): void => {
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (isTypingTarget(e.target)) return

      // Reserve Space for hold-to-pan: stop a focused button from
      // re-firing its click on each Space press (e.g. the sidebar
      // trigger after it's been clicked). The lib's pan handler reads
      // its own keydown listener, so this doesn't affect panning.
      if (e.code === "Space" && isButtonTarget(e.target)) {
        e.preventDefault()
        return
      }

      const meta = e.metaKey || e.ctrlKey

      if (meta && (e.key === "z" || e.key === "Z")) {
        e.preventDefault()
        if (e.shiftKey) store.redo()
        else store.undo()
        return
      }
      if (meta && (e.key === "y" || e.key === "Y")) {
        e.preventDefault()
        store.redo()
        return
      }
      // ⌘/Ctrl+F → open the "Find on board" dialog. Handled before the
      // meta early-return below so the browser's native find doesn't
      // intercept it. Skipped while typing (guarded at the top).
      if (meta && (e.key === "f" || e.key === "F")) {
        e.preventDefault()
        const app = useBoardAppStore.getState()
        app.setChromeDialog("find-node")
        return
      }

      if (meta || e.altKey || e.shiftKey) return

      const key = e.key.toLowerCase()
      const app = useBoardAppStore.getState()

      const tool = TOOL_SHORTCUTS[key]
      if (tool) {
        e.preventDefault()
        app.setTool(tool)
        return
      }

      if (key === "s") {
        e.preventDefault()
        app.setChromeDialog(app.chromeDialog === "shape-menu" ? null : "shape-menu")
        return
      }
      if (key === "g") {
        e.preventDefault()
        app.setChromeDialog("icon-search")
        return
      }
      if (key === "i") {
        e.preventDefault()
        app.setChromeDialog("image-search")
        return
      }
      if (key === "m") {
        e.preventDefault()
        app.setSlidesPanelOpen(!app.slidesPanelOpen)
      }
    }

    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [store])
}
