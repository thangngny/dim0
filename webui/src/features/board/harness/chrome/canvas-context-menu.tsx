import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type RefObject,
} from "react"
import { toast } from "sonner"
import type { CanvasStore, NodeId, Renderer } from "@canvas-harness/core"
import { exportSelection, exportSelectionSvg } from "@canvas-harness/core"
import {
  ArrowSquareUpRight as ArrowSquareUpRightIcon,
  Clipboard as ClipboardIcon,
  SquaresFour as SquaresFourIcon,
  StackMinus as StackMinusIcon,
  StackPlus as StackPlusIcon,
} from "@phosphor-icons/react"
import {
  ArticleSummaryIcon,
  ChatTranslateIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  DrawIcon,
  ImagePlaceholderIcon,
  SchemaMapIcon,
  SparklesIcon,
  TreeMapIcon,
} from "@/components/icons"
import { buildContextTextFromNodes } from "@/features/board/utils/context-text"
import { useAiSparkActions } from "@/features/board/hooks/use-ai-spark-actions"
import { useBoardAppStore } from "../store/board-app-store"
import { nodeToNote } from "../convert/node-to-note"
import { alignNodes, type AlignMode } from "../canvas/align-nodes"
import { groupAndCollapse } from "../canvas/cluster-overlay"
import type { NoteNode } from "@/features/board/types/flow"


export type CanvasContextMenuProps = {
  /** Canvas wrap ref — the menu's contextmenu listener attaches here. */
  wrapRef: RefObject<HTMLElement | null>
  store: CanvasStore
  /**
   * Renderer ref — read at export time to pass the live asset cache
   * into `exportSelection`. Without the cache, image and icon nodes
   * are silently skipped in the PNG output (canvas-harness 0.1.15+).
   */
  rendererRef: RefObject<Renderer | null>
}


/** Common languages shown as one-click chips in the Translate submenu. */
const COMMON_LANGUAGES: ReadonlyArray<string> = [
  "English",
  "French",
  "Spanish",
  "Chinese",
  "Japanese",
  "Korean",
  "German",
  "Portuguese",
]


type AiSparkVisual = {
  key: string
  Icon: typeof ArticleSummaryIcon
}


/** Icon per AI Spark action — keeps the menu visually scannable. */
const AI_SPARK_VISUALS: ReadonlyArray<AiSparkVisual> = [
  { key: "summarize", Icon: ArticleSummaryIcon },
  { key: "mapify", Icon: TreeMapIcon },
  { key: "schemify", Icon: SchemaMapIcon },
  { key: "quizify", Icon: ClipboardIcon },
  { key: "drawify", Icon: DrawIcon },
  { key: "explain", Icon: SparklesIcon },
]


/**
 * Build the structured context text for the agent from the current
 * canvas selection. Skips edges (the agent only consumes nodes).
 */
const buildSelectedContextText = (
  store: CanvasStore,
  opts: { skipPrefix?: boolean } = {},
): string => {
  const ids = store.getSelection()
  const synthetic: NoteNode[] = []
  for (const id of ids) {
    const node = store.getNode(id as NodeId)
    if (!node) continue
    const note = nodeToNote(node)
    synthetic.push({ id: note.id, data: note } as unknown as NoteNode)
  }
  if (synthetic.length === 0) return ""
  return buildContextTextFromNodes(synthetic, opts).trim()
}


/**
 * Right-click context menu for the canvas-harness board. Mirrors
 * prod's `graph-context-menu.tsx` — Position / Export / AI Spark /
 * Translate sections — wired entirely against the harness store +
 * lib export helpers (no react-flow).
 */
export function CanvasContextMenu({ wrapRef, store, rendererRef }: CanvasContextMenuProps) {
  const boardId = useBoardAppStore((s) => s.boardId)
  const setChromeDialog = useBoardAppStore((s) => s.setChromeDialog)
  const [menuPos, setMenuPos] = useState<{ x: number; y: number } | null>(null)
  const [aiOpen, setAiOpen] = useState(false)
  const [translateOpen, setTranslateOpen] = useState(false)
  const [exportTransparent, setExportTransparent] = useState(false)
  const [customLanguage, setCustomLanguage] = useState("")
  const menuRef = useRef<HTMLDivElement | null>(null)
  const { actions: aiActions, processingKey, runAction } = useAiSparkActions()

  const aiMenuActions = useMemo(
    () => aiActions.filter((a) => a.key !== "translate"),
    [aiActions],
  )

  const closeMenu = useCallback(() => {
    setMenuPos(null)
    setAiOpen(false)
    setTranslateOpen(false)
  }, [])

  // Right-click trigger — only opens when something is selected.
  useEffect(() => {
    const wrap = wrapRef.current
    if (!wrap) return
    const onContext = (e: MouseEvent): void => {
      const target = e.target as HTMLElement | null
      // Skip when right-clicking inside an editor input.
      if (
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable)
      ) {
        return
      }
      if (store.getSelection().length === 0) return
      e.preventDefault()
      setMenuPos({ x: e.clientX, y: e.clientY })
      setAiOpen(false)
      setTranslateOpen(false)
    }
    wrap.addEventListener("contextmenu", onContext)
    return () => wrap.removeEventListener("contextmenu", onContext)
  }, [wrapRef, store])

  // Close on outside click / Esc.
  useEffect(() => {
    if (!menuPos) return
    const onDown = (e: MouseEvent): void => {
      const target = e.target as HTMLElement | null
      if (target && menuRef.current?.contains(target)) return
      closeMenu()
    }
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") closeMenu()
    }
    window.addEventListener("mousedown", onDown, true)
    window.addEventListener("keydown", onKey)
    return () => {
      window.removeEventListener("mousedown", onDown, true)
      window.removeEventListener("keydown", onKey)
    }
  }, [menuPos, closeMenu])

  const selection = useCallback(() => store.getSelection(), [store])

  // ---- Position ---------------------------------------------------------
  const handleSendBackward = useCallback(() => {
    store.sendBackward(selection())
    closeMenu()
  }, [store, selection, closeMenu])
  const handleSendForward = useCallback(() => {
    store.bringForward(selection())
    closeMenu()
  }, [store, selection, closeMenu])
  const handleSendToBack = useCallback(() => {
    store.sendToBack(selection())
    closeMenu()
  }, [store, selection, closeMenu])
  const handleSendToFront = useCallback(() => {
    store.bringToFront(selection())
    closeMenu()
  }, [store, selection, closeMenu])

  // ---- Align / distribute ----------------------------------------------
  // Snap the selected nodes to a common edge/center, or space them evenly.
  // Only the moved axis is patched so a horizontal align preserves each
  // node's vertical position. Requires ≥2 nodes (≥3 for distribute).
  const handleAlign = useCallback(
    (mode: AlignMode) => {
      const ids = selection()
      const nodes = ids
        .map((id) => store.getNode(id as NodeId))
        .filter((n): n is NonNullable<typeof n> => !!n)
      const patches = alignNodes(nodes, mode)
      for (const [id, patch] of patches) {
        store.updateNode(id as NodeId, patch)
      }
      closeMenu()
    },
    [store, selection, closeMenu],
  )

  const selectedCount = selection().length
  const canAlign = selectedCount >= 2
  const canDistribute = selectedCount >= 3

  // ---- Group & collapse cluster ---------------------------------------
  // Bundle the selected nodes into a named group and hide them; the
  // ClusterOverlay renders an Expand proxy at the centroid. Requires ≥2
  // nodes (a one-node cluster is pointless).
  const handleGroupCollapse = useCallback(() => {
    const ids = selection()
    const nodes = ids
      .map((id) => store.getNode(id as NodeId))
      .filter((n): n is NonNullable<typeof n> => !!n)
    if (nodes.length < 2 || !boardId) return
    const name = window.prompt("Cluster name", `Cluster ${store.getAllGroups().length + 1}`)
    groupAndCollapse(store, nodes, boardId, name ?? undefined)
    closeMenu()
  }, [store, selection, closeMenu, boardId])

  // ---- Export -----------------------------------------------------------
  const handleExportPng = useCallback(async () => {
    try {
      const blob = await exportSelection(store, {
        transparentBackground: exportTransparent,
        // Pass the live renderer's asset cache so image + icon nodes
        // paint from already-decoded bitmaps. Without this, the lib
        // silently skips those node types in the output (back-compat
        // shape from canvas-harness 0.1.15).
        assetCache: rendererRef.current?.getAssetCache(),
      })
      try {
        // Try clipboard first (Notion / Figma-style behavior).
        await navigator.clipboard.write([
          new ClipboardItem({ "image/png": blob }),
        ])
        toast.success("Copied to clipboard")
      } catch {
        // Fall back to file download.
        const url = URL.createObjectURL(blob)
        Object.assign(document.createElement("a"), {
          href: url,
          download: "selection.png",
        }).click()
        URL.revokeObjectURL(url)
      }
    } catch (err) {
      console.error("[context-menu] PNG export failed", err)
      toast.error("Couldn't export selection")
    } finally {
      closeMenu()
    }
  }, [store, exportTransparent, closeMenu, rendererRef])

  const handleExportSvg = useCallback(() => {
    try {
      const svg = exportSelectionSvg(store)
      const blob = new Blob([svg], { type: "image/svg+xml" })
      const url = URL.createObjectURL(blob)
      Object.assign(document.createElement("a"), {
        href: url,
        download: "selection.svg",
      }).click()
      URL.revokeObjectURL(url)
    } catch (err) {
      console.error("[context-menu] SVG export failed", err)
      toast.error("Couldn't export selection")
    } finally {
      closeMenu()
    }
  }, [store, closeMenu])

  // ---- AI Spark / Translate ---------------------------------------------
  const handleAiAction = useCallback(
    async (actionKey: string) => {
      if (!boardId) {
        toast.error("Select a board first.")
        return
      }
      const contextText = buildSelectedContextText(store)
      if (!contextText) {
        toast.error("Select at least one node with content.")
        return
      }
      closeMenu()
      await runAction({ boardId, contextText, actionKey })
    },
    [boardId, store, runAction, closeMenu],
  )

  const handleTranslate = useCallback(
    async (language: string) => {
      if (!boardId) {
        toast.error("Select a board first.")
        return
      }
      const contextText = buildSelectedContextText(store, { skipPrefix: true })
      if (!contextText) {
        toast.error("Select at least one node with content.")
        return
      }
      closeMenu()
      await runAction({
        boardId,
        contextText,
        actionKey: "translate",
        targetLanguage: language,
      })
    },
    [boardId, store, runAction, closeMenu],
  )

  if (!menuPos) return null

  return (
    <div
      ref={menuRef}
      role="menu"
      className="fixed z-[60] min-w-[200px] max-w-[320px] rounded-lg border bg-popover p-1 text-sm text-popover-foreground shadow-lg"
      style={{ top: menuPos.y, left: menuPos.x }}
      onContextMenu={(e) => e.preventDefault()}
      onMouseDown={(e) => e.stopPropagation()}
    >
      <SectionLabel>Position</SectionLabel>
      <MenuButton icon={StackMinusIcon} label="Send backward" onClick={handleSendBackward} />
      <MenuButton icon={StackPlusIcon} label="Send forward" onClick={handleSendForward} />
      <MenuButton icon={StackMinusIcon} label="Send to back" onClick={handleSendToBack} />
      <MenuButton icon={StackPlusIcon} label="Send to front" onClick={handleSendToFront} />

      {canAlign && (
        <>
          <Divider />
          <SectionLabel>Align{` (${selectedCount})`}</SectionLabel>
          <div className="grid grid-cols-3 gap-1 px-2 py-1">
            <AlignButton label="Left" onClick={() => handleAlign("left")} />
            <AlignButton label="Center" onClick={() => handleAlign("center-h")} />
            <AlignButton label="Right" onClick={() => handleAlign("right")} />
            <AlignButton label="Top" onClick={() => handleAlign("top")} />
            <AlignButton label="Middle" onClick={() => handleAlign("middle-v")} />
            <AlignButton label="Bottom" onClick={() => handleAlign("bottom")} />
          </div>
          {canDistribute && (
            <div className="grid grid-cols-2 gap-1 px-2 pb-1">
              <AlignButton label="Distribute ↔" onClick={() => handleAlign("distribute-h")} />
              <AlignButton label="Distribute ↕" onClick={() => handleAlign("distribute-v")} />
            </div>
          )}
          <MenuButton
            icon={SquaresFourIcon}
            label="Group & collapse selected"
            onClick={handleGroupCollapse}
          />
        </>
      )}

      {selectedCount >= 1 && (
        <>
          <Divider />
          <MenuButton
            icon={ArrowSquareUpRightIcon}
            label="Copy to board…"
            onClick={() => { setChromeDialog("copy-to-board"); closeMenu() }}
          />
        </>
      )}

      <Divider />
      <SectionLabel>Export</SectionLabel>
      <MenuButton
        icon={ClipboardIcon}
        label="Copy selected as PNG"
        onClick={() => void handleExportPng()}
      />
      <div className="flex items-center justify-between gap-2 px-3 py-1.5 text-xs text-muted-foreground">
        <span>Transparent background</span>
        <div className="inline-flex items-center rounded-md border border-border bg-background p-0.5">
          <button
            type="button"
            className={`h-6 rounded-sm px-2 transition-colors ${
              exportTransparent
                ? "text-muted-foreground hover:text-foreground"
                : "bg-muted text-foreground"
            }`}
            onClick={() => setExportTransparent(false)}
          >
            Off
          </button>
          <button
            type="button"
            className={`h-6 rounded-sm px-2 transition-colors ${
              exportTransparent
                ? "bg-muted text-foreground"
                : "text-muted-foreground hover:text-foreground"
            }`}
            onClick={() => setExportTransparent(true)}
          >
            On
          </button>
        </div>
      </div>
      <MenuButton
        icon={ImagePlaceholderIcon}
        label="Download as SVG"
        onClick={handleExportSvg}
      />

      <Divider />
      <button
        type="button"
        className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left hover:bg-muted"
        onClick={() => setAiOpen((o) => !o)}
      >
        <SparklesIcon className="size-4 text-secondary-foreground" weight="fill" />
        <span>AI</span>
        <span className="ml-auto text-xs text-muted-foreground">
          {aiOpen ? (
            <ChevronDownIcon className="size-3" />
          ) : (
            <ChevronRightIcon className="size-3" />
          )}
        </span>
      </button>
      {aiOpen && (
        <div className="ml-2 border-l border-border/60 pl-1">
          {aiMenuActions.map((action) => {
            const visual = AI_SPARK_VISUALS.find((v) => v.key === action.key)
            const Icon = visual?.Icon ?? SparklesIcon
            return (
              <MenuButton
                key={action.key}
                icon={Icon}
                label={action.label}
                disabled={!!processingKey}
                badge={action.key === "drawify" ? "Beta" : undefined}
                iconClassName="text-secondary-foreground"
                onClick={() => void handleAiAction(action.key)}
              />
            )
          })}
          <button
            type="button"
            className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left hover:bg-muted"
            onClick={() => setTranslateOpen((o) => !o)}
          >
            <ChatTranslateIcon className="size-4 text-secondary-foreground" />
            <span>Translate</span>
            <span className="ml-auto text-xs text-muted-foreground">
              {translateOpen ? (
                <ChevronDownIcon className="size-3" />
              ) : (
                <ChevronRightIcon className="size-3" />
              )}
            </span>
          </button>
          {translateOpen && (
            <div className="flex flex-col gap-2 px-3 pb-2 pt-1">
              <div className="flex items-center gap-2">
                <input
                  className="h-7 flex-1 rounded-md border border-input bg-background px-2 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-secondary-foreground/30"
                  placeholder="Custom language…"
                  value={customLanguage}
                  onChange={(e) => setCustomLanguage(e.target.value)}
                  onMouseDown={(e) => e.stopPropagation()}
                />
                <button
                  type="button"
                  className="h-7 rounded-sm border border-border bg-muted/70 px-2 text-xs font-medium hover:text-secondary-foreground"
                  onClick={() =>
                    void handleTranslate(customLanguage.trim() || "English")
                  }
                >
                  Go
                </button>
              </div>
              <div className="flex flex-wrap gap-1">
                {COMMON_LANGUAGES.map((language) => (
                  <button
                    key={language}
                    type="button"
                    className="rounded-sm bg-muted px-2 py-1 text-xs font-medium hover:bg-muted/70"
                    onClick={() => void handleTranslate(language)}
                  >
                    {language}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}


type MenuButtonProps = {
  icon: typeof ArticleSummaryIcon
  label: string
  onClick: () => void
  disabled?: boolean
  badge?: string
  iconClassName?: string
}


function MenuButton({
  icon: Icon,
  label,
  onClick,
  disabled,
  badge,
  iconClassName = "",
}: MenuButtonProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
    >
      <Icon className={`size-4 shrink-0 ${iconClassName}`} />
      <span>{label}</span>
      {badge ? (
        <span className="ml-auto rounded-sm border border-border px-2 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
          {badge}
        </span>
      ) : null}
    </button>
  )
}


function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-3 py-1 text-xs font-medium text-muted-foreground">
      {children}
    </div>
  )
}


/** Compact label-only button used in the Align grid. */
function AlignButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-md border border-border bg-background px-2 py-1.5 text-xs font-medium hover:bg-muted"
    >
      {label}
    </button>
  )
}


function Divider() {
  return <div className="my-1 h-px bg-border" />
}
