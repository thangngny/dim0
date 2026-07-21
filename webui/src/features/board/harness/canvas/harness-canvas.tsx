import { useCallback, useEffect, useRef, useState } from "react"
import { useNavigate } from "@tanstack/react-router"
import { useQueryClient } from "@tanstack/react-query"
import { hitTestAny, type CanvasStore, type NodeId, type Renderer } from "@canvas-harness/core"
import { createDefaultNote } from "@/features/board/types/note"
import { noteToNode } from "../convert/note-to-node"
import { applyStyleMemory } from "./use-create-handlers"
import { createHarnessTextareaEditor } from "./text-editor-adapter"
import { setAgentBridge } from "../agent/agent-bridge"
import {
  applyChangeKindOutput,
  applyDeleteSubtreeOutput,
  applyLinkOutput,
  applyMergeNotesOutput,
  applyNoteOutput,
  applyRelayoutOutput,
  applyReparentNoteOutput,
  applySplitNoteOutput,
} from "../agent/apply-tool-output"
import { useHarnessApplyMindMap } from "../agent/use-harness-apply-mindmap"
import { setCanvasStoreRef } from "../canvas-store-ref"
import {
  Canvas,
  CanvasProvider,
  Minimap,
  type ArrowToolDefaults,
  type CanvasPointerEvent,
} from "@canvas-harness/react"
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import {
  CanvasContextMenu,
  EmptyBoardCoachmarks,
  HarnessCollabStatus,
  HarnessPeerChip,
  HarnessReadonlyChip,
  HarnessToolbar,
  HarnessViewportControls,
  NodeSurfaceHost,
  PresentationControls,
  RemoteCursors,
  SlidesPanel,
  StyleSidebar,
} from "../chrome"
import { LinearView, ListView } from "../views"
import { boardNodeTypes, useRenderCustomNodeView } from "../node-types"
import { hydrateBoardStore } from "../persist/snapshot-load"
import { ShareButton } from "@/features/sharing/share-button"
import { useBoardAppStore } from "../store/board-app-store"
import { createBoardStore } from "../store/create-board-store"
import { adaptEdgeColors, applyColorsToEdgeStyle } from "../theme/color-adapter"
import { getBoardThemeMode } from "../theme/theme-mode-ref"
import { useBoardTheme } from "../theme/use-board-theme"
import { useThemeColorProjection } from "../theme/use-theme-color-projection"
import { useBoardKeyboard } from "./use-board-keyboard"
import { useCenterFromUrl } from "./use-center-from-url"
import { useCreateHandlers } from "./use-create-handlers"
import { useHarnessDropFiles } from "./use-drop-files"
import { useHydrateIconNodes } from "./use-hydrate-icon-nodes"
import { usePresentationMode } from "./use-presentation-mode"
import { useBlockFolderCopy } from "./use-block-folder-copy"
import { resolveStoredEdgeColors, useStampNewEdges } from "./use-stamp-new-edges"
import { useStampNewNodes } from "./use-stamp-new-nodes"
import { useStyleMemory } from "./use-style-memory"
import { CUSTOM_NODE_TYPES } from "./custom-node-types"
import { useLocalPresence } from "./use-local-presence"
import { useWsCollab } from "./use-ws-collab"
import { useThumbnailCapture } from "./use-thumbnail-capture"
import { useViewportPersistence } from "./use-viewport-persistence"
import { HarnessWrapRefProvider } from "./wrap-ref-provider"


/**
 * Canvas-harness mount for the Dim0 board. One per board view; the
 * canvas-harness store is created lazily and persists across re-renders
 * for the same component instance.
 *
 * Responsibilities:
 *  - Create the canvas store with the custom node-type registry
 *  - Hydrate from the board API on scope change (board-app-store boardId/rootId)
 *  - Subscribe the debounced save once hydration completes
 *  - Wire theme + selection chrome + minimap from useBoardTheme
 *  - Dispatch custom node views via the central router
 *
 * Tool state, top-bar wiring, keyboard shortcuts land in subsequent
 * phase-4 commits.
 */
export function HarnessCanvas() {
  const boardId = useBoardAppStore((s) => s.boardId)
  const rootId = useBoardAppStore((s) => s.rootId)
  const canEdit = useBoardAppStore((s) => s.canEdit)
  const setIsLoading = useBoardAppStore((s) => s.setIsLoading)
  const setCanEdit = useBoardAppStore((s) => s.setCanEdit)
  const setBoardRole = useBoardAppStore((s) => s.setBoardRole)
  const setBoardLabel = useBoardAppStore((s) => s.setBoardLabel)
  const setBoardVisibility = useBoardAppStore((s) => s.setBoardVisibility)

  const storeRef = useRef<CanvasStore | null>(null)
  if (!storeRef.current) {
    storeRef.current = createBoardStore({ nodeTypes: [...boardNodeTypes] })
  }
  const store = storeRef.current

  const tool = useBoardAppStore((s) => s.tool)
  const viewMode = useBoardAppStore((s) => s.viewMode)
  const theme = useBoardTheme()
  const [ready, setReady] = useState(false)
  const wrapRef = useRef<HTMLDivElement>(null)
  // Captured via `<Canvas onRenderer>`; presentation mode toggles
  // `setHideFrames` on this so slide chrome (border + label) drops out
  // and only the contents show.
  const rendererRef = useRef<Renderer | null>(null)
  // Stable so `<Canvas>` doesn't tear down + recreate its renderer on
  // every re-render. A fresh renderer always starts with frames shown,
  // so re-apply the present-mode hide here — otherwise a re-render
  // mid-presentation (e.g. chrome hiding) would resurrect the frames.
  const handleRenderer = useCallback((r: Renderer) => {
    rendererRef.current = r
    if (useBoardAppStore.getState().presentationMode) r.setHideFrames(true)
  }, [])
  const queryClient = useQueryClient()

  // Bridge for the agent's post-stream apply block (lives outside this
  // component tree). Closures capture the current store + scope so the
  // applier doesn't need access to React state. Cleared on unmount /
  // scope change.
  useEffect(() => {
    if (!boardId) {
      setAgentBridge(null)
      return
    }
    setAgentBridge({
      applyNoteOutput: (output) =>
        applyNoteOutput(store, queryClient, boardId, rootId, output),
      applyLinkOutput: (output) =>
        applyLinkOutput(store, boardId, output),
      applyChangeKindOutput: (output) =>
        applyChangeKindOutput(store, queryClient, boardId, rootId, output),
      applyReparentNoteOutput: (output) =>
        applyReparentNoteOutput(store, queryClient, boardId, rootId, output),
      applyDeleteSubtreeOutput: (output) =>
        applyDeleteSubtreeOutput(store, queryClient, boardId, rootId, output),
      applyMergeNotesOutput: (output) =>
        applyMergeNotesOutput(store, queryClient, boardId, rootId, output),
      applySplitNoteOutput: (output) =>
        applySplitNoteOutput(store, queryClient, boardId, rootId, output),
      applyRelayoutOutput: (output) =>
        applyRelayoutOutput(store, queryClient, boardId, rootId, output),
    })
    return () => setAgentBridge(null)
  }, [store, queryClient, boardId, rootId])

  // Module-level store ref — lets non-React code (buildMessageContext,
  // other agent helpers) reach the active store without prop-drilling.
  useEffect(() => {
    setCanvasStoreRef(store)
    return () => setCanvasStoreRef(null)
  }, [store])

  useBoardKeyboard(store)
  useViewportPersistence(store, boardId, rootId, ready)
  useCenterFromUrl(store, wrapRef, ready)
  const styleMemory = useStyleMemory(store)
  useStampNewEdges(store, boardId, rootId)
  useStampNewNodes(store, boardId, rootId)
  useBlockFolderCopy(store)
  useHarnessApplyMindMap(store, boardId, rootId)
  useHydrateIconNodes(store, boardId, rootId, ready)
  useThemeColorProjection(store, ready)
  useThumbnailCapture(store, boardId, ready, theme.minimap)
  usePresentationMode(store, wrapRef, rendererRef)

  // Collab is the only edit path (collab-archi §1). The WS adapter is
  // mounted unconditionally; presence + local cursor tracking go with it.
  // The server is the sole writer — no local REST save loop, so no
  // save-status pill either.
  useWsCollab(store, boardId, ready, rootId)
  useLocalPresence(store, wrapRef, ready)

  const { handleCreateDrag, handleClick } = useCreateHandlers(store, boardId, rootId, styleMemory)
  // Recompute every render — NOT memoized on `styleMemory`. The memory
  // object is intentionally identity-stable for its whole lifetime, so a
  // `useMemo([styleMemory])` would compute once at mount and freeze the
  // edge defaults at their initial (usually empty) value, ignoring every
  // later restyle. The lib reads `arrowDefaults` lazily at edge-draw time
  // (defaultsRef) and keys no effect off it, so a fresh object per render
  // is cheap and is what lets sticky edge styles actually take effect.
  // Factories run lazily at edge-draw time (canvas-harness 0.1.24+), so
  // a fresh arrow-drawn edge enters the store already stamped with
  // scope, version, stored colors, and theme-projected display style.
  // That lets useStampNewEdges' init branch go away — Cmd+Z on a fresh
  // edge now reverts the create in one press.
  const arrowDefaults: ArrowToolDefaults = {
    pathStyle: styleMemory.getEdgePathStyle(),
    style: () => {
      const base = styleMemory.getEdgeStyle() ?? {}
      const stored = resolveStoredEdgeColors(styleMemory.getEdgeStoredColors())
      const mode = getBoardThemeMode()
      const display = mode === "dark" ? adaptEdgeColors(stored, "dark") : stored
      return applyColorsToEdgeStyle(base, display)
    },
    data: () => {
      if (!boardId) return undefined
      return {
        version: 1,
        createdAt: new Date().toISOString(),
        _storedColors: resolveStoredEdgeColors(styleMemory.getEdgeStoredColors()),
        graphUid: boardId,
        parentId: rootId ?? undefined,
      }
    },
  }
  const { onDragOver, onDrop } = useHarnessDropFiles(wrapRef, store, boardId, rootId, canEdit)
  const navigate = useNavigate()

  // Double-click dispatch.
  //
  // canvas-harness's <Canvas> dblclick handler runs BEFORE this consumer
  // callback (see lib's Canvas.tsx) and already does the right thing
  // for every hit kind: node body / edge body / edge label all call
  // `beginEdit(targetId)`; midpoint-handle restores auto-route. So the
  // consumer only needs to (a) override for Dim0's custom node types
  // where the editing surface lives elsewhere, and (b) handle the
  // "truly empty space" case by dropping a quick text node.
  //
  // Earlier this handler branched only on `"nodeId" in hit` and treated
  // edge hits as empty space — which meant a dbl-click on an edge label
  // would clobber the lib's just-started edge-label edit with a brand-new
  // text node. The `if (hit) return` short-circuit (matching the
  // playground) is what prevents that.
  const handleDoubleClick = useCallback(
    (e: CanvasPointerEvent): void => {
      const camera = store.getCamera()
      const hit = hitTestAny(store, e.world, camera.z)

      if (hit) {
        if ("nodeId" in hit) {
          // Override for custom node types where Dim0 owns the editing
          // surface elsewhere (sheet / code-sandbox / widget open via
          // panel; folder dbl-click navigates into the folder; document
          // has no inline editor). Default node types fall through and
          // keep the lib's inline editor.
          const node = store.getNode(hit.nodeId)
          if (node && CUSTOM_NODE_TYPES.has(node.type)) {
            store.cancelEdit()
            if (node.type === "folder" && boardId) {
              navigate({
                to: "/boards/$id",
                params: { id: boardId },
                search: (prev: Record<string, unknown>) => ({
                  ...prev,
                  root_id: node.id,
                }),
              })
            }
          }
        }
        // Edge hits: lib already called `beginEdit(edgeId)` — that's
        // exactly the edge-label editor we want. Nothing further to do.
        return
      }

      // Truly-empty space: quick text node + edit. Only in the select
      // tool — other tools have their own click semantics.
      if (e.tool !== "select" || !canEdit || !boardId) return
      const note = createDefaultNote({ boardId, nodeType: "text" })
      if (rootId) note.parentId = rootId
      const size = note.properties.nodeSize?.size ?? { width: 150, height: 20 }
      note.properties.nodePosition = {
        type: "position",
        position: { x: e.world.x - size.width / 2, y: e.world.y - size.height / 2 },
      }
      const styled = applyStyleMemory(noteToNode(note), styleMemory)
      store.addNode(styled)
      store.setSelection([styled.id as NodeId])
      store.beginEdit(styled.id as NodeId)
    },
    [store, boardId, rootId, canEdit, navigate, styleMemory],
  )

  // Hydrate on scope change. `cancelled` guards against late-arriving fetches
  // when the user navigates rapidly between boards.
  useEffect(() => {
    if (!boardId) {
      setReady(false)
      return
    }
    let cancelled = false
    setReady(false)
    setIsLoading(true)
    hydrateBoardStore(store, {
      boardId,
      rootId: rootId ?? undefined,
      isCancelled: () => cancelled,
    })
      .then(({ graph, canEdit, role }) => {
        if (cancelled) return
        setCanEdit(canEdit)
        setBoardRole(role)
        setBoardLabel(graph.label ?? "")
        if (graph.visibility === "private" || graph.visibility === "public") {
          setBoardVisibility(graph.visibility)
        }
      })
      .catch((err) => {
        if (!cancelled) console.error("[harness] hydrate failed", err)
      })
      .finally(() => {
        if (cancelled) return
        setIsLoading(false)
        setReady(true)
      })
    return () => {
      cancelled = true
    }
  }, [boardId, rootId, store, setIsLoading, setCanEdit, setBoardRole, setBoardLabel, setBoardVisibility])

  return (
    <CanvasProvider store={store}>
      <HarnessWrapRefProvider value={wrapRef}>
        <div
          ref={wrapRef}
          className="absolute inset-0"
          onDragOver={onDragOver}
          onDrop={onDrop}
        >
          <HarnessCanvasInner
            theme={theme}
            tool={tool}
            ready={ready}
            viewMode={viewMode}
            arrowDefaults={arrowDefaults}
            onCreateDrag={handleCreateDrag}
            onClick={handleClick}
            onDoubleClick={handleDoubleClick}
            onRenderer={handleRenderer}
          />
          <CanvasContextMenu wrapRef={wrapRef} store={store} rendererRef={rendererRef} />
        </div>
      </HarnessWrapRefProvider>
    </CanvasProvider>
  )
}


type InnerProps = {
  theme: ReturnType<typeof useBoardTheme>
  tool: string
  ready: boolean
  viewMode: "board" | "files" | "list"
  arrowDefaults: ArrowToolDefaults
  onCreateDrag: ReturnType<typeof useCreateHandlers>["handleCreateDrag"]
  onClick: ReturnType<typeof useCreateHandlers>["handleClick"]
  onDoubleClick: (e: CanvasPointerEvent) => void
  onRenderer: (r: Renderer) => void
}


function HarnessCanvasInner({
  theme,
  tool,
  ready,
  viewMode,
  arrowDefaults,
  onCreateDrag,
  onClick,
  onDoubleClick,
  onRenderer,
}: InnerProps) {
  const renderView = useRenderCustomNodeView()
  const isBoard = viewMode === "board"
  // Hide non-essential chrome (toolbar, viewport controls, minimap)
  // during presentation so the canvas reads as a clean slide. Other
  // already-self-hiding chrome (readonly chip, top-right strip) stays
  // managed by their own components.
  const presenting = useBoardAppStore((s) => s.presentationMode)
  return (
    <>
      {isBoard ? (
        <>
          <Canvas
            tool={tool}
            theme={theme.resolver}
            selectionColor={theme.selectionColor}
            background={theme.background}
            renderCustomNodeView={renderView}
            editorAdapter={createHarnessTextareaEditor}
            arrowDefaults={arrowDefaults}
            onCreateDrag={onCreateDrag}
            onClick={onClick}
            onDoubleClick={onDoubleClick}
            onRenderer={onRenderer}
          />
          {/*
            Paper grain over the canvas surface: sits above the drawn
            scene (z-index 1) but below all chrome (z-50+), pointer-events
            none so it never intercepts interaction. Hidden while
            presenting for clean slides.
          */}
          {!presenting && <div className="board-paper-grain" aria-hidden="true" />}
          {!presenting && (
            <Minimap
              width={200}
              height={140}
              viewportColor={theme.minimap.viewportColor}
              backgroundColor={theme.minimap.backgroundColor}
              borderColor={theme.minimap.borderColor}
              defaultNodeColor={theme.minimap.defaultNodeColor}
              style={{
                position: "absolute",
                bottom: 16,
                right: 16,
                borderRadius: 6,
                overflow: "hidden",
                zIndex: 50,
              }}
            />
          )}
          {!presenting && <HarnessViewportControls />}
          <StyleSidebar />
          <RemoteCursors />
          <EmptyBoardCoachmarks ready={ready} />
        </>
      ) : viewMode === "files" ? (
        <LinearView />
      ) : (
        <ListView />
      )}
      {/*
        Always-mounted chrome: toolbar (with view dropdown), save
        status badge, slide-related surfaces. NodeSurfaceHost stays
        mounted everywhere so the modal editor opens from any view.
      */}
      {!presenting && <HarnessToolbar />}
      {/*
        Top-right chrome row: save status + share button live in one
        flex container so they never overlap (z-stack collisions cost
        us once already). Each child decides whether to render itself
        — the container is invisible when empty.
      */}
      <div className="absolute right-3 top-3 z-50 flex items-center gap-2">
        <HarnessPeerChip />
        <HarnessReadonlyChip />
        <HarnessCollabStatus />
        <ShareButton />
      </div>
      <NodeSurfaceHost />
      <SlidesSheet />
      <PresentationOverlay />
    </>
  )
}


/**
 * Right-side Sheet hosting the slides panel. Open state lives on the
 * app store so the toolbar button + keyboard shortcut can toggle it.
 * `modal={false}` + no overlay keeps the canvas interactive while the
 * panel is up (you can still pan / pick a slide on the canvas).
 */
function SlidesSheet() {
  const open = useBoardAppStore((s) => s.slidesPanelOpen)
  const setOpen = useBoardAppStore((s) => s.setSlidesPanelOpen)
  return (
    <Sheet open={open} onOpenChange={setOpen} modal={false}>
      <SheetContent
        side="right"
        showOverlay={false}
        showClose={false}
        className="w-[360px] max-w-[92vw] border-l border-border bg-sidebar p-0 text-sidebar-foreground"
      >
        <SheetHeader className="sr-only">
          <SheetTitle>Slides</SheetTitle>
        </SheetHeader>
        <SlidesPanel />
      </SheetContent>
    </Sheet>
  )
}


/** Floating bottom-center controls, only mounted while presenting. */
function PresentationOverlay() {
  const presenting = useBoardAppStore((s) => s.presentationMode)
  if (!presenting) return null
  return <PresentationControls />
}
