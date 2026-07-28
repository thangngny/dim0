import { useEffect, useMemo, useState } from "react"
import { ArrowSquareUpRightIcon } from "@phosphor-icons/react"
import type { NodeId } from "@canvas-harness/core"
import { useCanvasStore } from "@canvas-harness/react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { listBoards } from "@/features/board/api/list-boards"
import { addNotes } from "@/features/board/api/add-notes"
import type { Note } from "@/features/board/types/note"
import { cn } from "@/lib/utils"
import { nodeToNote } from "../convert/node-to-note"
import { useBoardAppStore } from "../store/board-app-store"


export type CopyToBoardDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
}


/** Fresh uuid for a copied note so it doesn't collide with the source id. */
const newId = (): string =>
  (globalThis.crypto?.randomUUID?.() ??
    `copy-${Date.now()}-${Math.random().toString(16).slice(2)}`)


/**
 * Feature F — copy the selected nodes to another board.
 *
 * Cross-board copy can't go through the per-board collab WS (it only
 * writes the current board), so this uses the REST `addNotes` path: it
 * converts each selected canvas Node to a Dim0 Note (`nodeToNote`),
 * mints a fresh id + points `graphUid` at the target board, and POSTs
 * the batch. Content, style, colors, and properties (position/size)
 * carry over; edges aren't copied (they'd reference ids that don't
 * exist on the target board).
 *
 * The board list comes from `listBoards`; the current board is filtered
 * out so the user doesn't copy onto itself.
 */
export function CopyToBoardDialog({ open, onOpenChange }: CopyToBoardDialogProps) {
  const store = useCanvasStore()
  const queryClient = useQueryClient()
  const currentBoardId = useBoardAppStore((s) => s.boardId)
  const [busy, setBusy] = useState(false)

  // Fetch the board list only while the dialog is open.
  const { data: boards, isLoading } = useQuery({
    queryKey: ["listBoards", "copy-to-board"],
    queryFn: () => listBoards(),
    enabled: open,
    staleTime: 30_000,
  })

  useEffect(() => {
    if (!open) setBusy(false)
  }, [open])

  const pickable = useMemo(
    () => (boards ?? []).filter((b) => b.uid !== currentBoardId),
    [boards, currentBoardId],
  )

  const handleCopy = async (targetBoardId: string, label: string): Promise<void> => {
    const ids = store.getSelection()
    const nodes = ids
      .map((id) => store.getNode(id as NodeId))
      .filter((n): n is NonNullable<typeof n> => !!n)
    if (nodes.length === 0) {
      toast.error("Select at least one node to copy.")
      return
    }
    setBusy(true)
    try {
      const copies: Note[] = nodes.map((n) => {
        const note = nodeToNote(n)
        note.id = newId()
        note.graphUid = targetBoardId
        note.parentId = undefined
        note.version = 1
        note.createdAt = undefined
        note.updatedAt = undefined
        return note
      })
      await addNotes(targetBoardId, copies)
      // Invalidate the target board's contents so a later open shows them.
      await queryClient.invalidateQueries({ queryKey: ["board", targetBoardId] })
      toast.success(`Copied ${copies.length} node${copies.length > 1 ? "s" : ""} to “${label}”.`)
      onOpenChange(false)
    } catch (err) {
      console.error("[copy-to-board] failed", err)
      toast.error("Couldn't copy — check the target board permissions.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[440px] gap-0 p-0">
        <DialogHeader className="sr-only">
          <DialogTitle>Copy to board</DialogTitle>
        </DialogHeader>
        <div className="border-b border-border px-4 py-3">
          <div className="text-sm font-medium text-foreground">Copy selected nodes to…</div>
          <div className="text-xs text-muted-foreground">
            {store.getSelection().length} node(s) · content + style carry over, edges don't.
          </div>
        </div>
        <div className="max-h-[320px] overflow-y-auto py-1">
          {isLoading ? (
            <div className="px-4 py-6 text-center text-sm text-muted-foreground">
              Loading boards…
            </div>
          ) : pickable.length === 0 ? (
            <div className="px-4 py-6 text-center text-sm text-muted-foreground">
              No other boards yet. Create one first.
            </div>
          ) : (
            pickable.map((b) => (
              <button
                key={b.uid}
                type="button"
                disabled={busy}
                onClick={() => void handleCopy(b.uid, b.label || "Untitled")}
                className={cn(
                  "flex w-full items-center gap-2 px-4 py-2 text-left text-sm hover:bg-secondary",
                  busy && "pointer-events-none opacity-60",
                )}
              >
                <ArrowSquareUpRightIcon className="size-4 shrink-0 text-muted-foreground" />
                <span className="truncate">{b.label || "Untitled"}</span>
                {b.role !== "owner" ? (
                  <span className="ml-auto rounded-sm bg-muted px-1.5 text-[10px] text-muted-foreground">
                    {b.role}
                  </span>
                ) : null}
              </button>
            ))
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}