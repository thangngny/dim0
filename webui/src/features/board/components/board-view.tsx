import { useEffect, useState } from "react"
import { useNavigate, useSearch } from "@tanstack/react-router"
import { HarnessCanvas } from "../harness/canvas"
import { useBoardAppStore } from "../harness/store/board-app-store"
import {
  consumeResearchDraft,
  consumeResearchHandoffFlag,
} from "../lib/research-handoff"
import { FloatingAssistant } from "./flow/floating-assistant/floating-assistant"
import { CopilotSheet } from "./flow/copilot-sheet"
import { ResearchHandoffBanner } from "./research-handoff-banner"


/**
 * Board entry-point. Mounts the canvas-harness board surface, the
 * floating-island AI composer at the bottom of the canvas, and the
 * full chat sheet drawer. Scope (boardId / rootId) is set on the
 * board-app-store by `BoardScreen`; this component reads from there.
 *
 * Research handoff (`?research=1` from launcher): open chat, show banner,
 * prefill draft via sessionStorage for one-click continue.
 */
export const BoardView: React.FC = () => {
  const navigate = useNavigate()
  const boardId = useBoardAppStore((s) => s.boardId)
  const chatSheetOpen = useBoardAppStore((s) => s.chatSheetOpen)
  const setChatSheetOpen = useBoardAppStore((s) => s.setChatSheetOpen)
  const presentationMode = useBoardAppStore((s) => s.presentationMode)

  // current_chat_id from the URL — shared between the floating island
  // and the full chat sheet so opening one continues the same chat.
  // research=1: handoff from research launcher.
  const boardSearch = useSearch({
    strict: false,
    select: (s: {
      current_chat_id?: string
      research?: string | number | boolean
      draft?: string
    }) => ({
      currentChatId: s.current_chat_id,
      research: s.research === "1" || s.research === 1 || s.research === true,
      draft: typeof s.draft === "string" ? s.draft : undefined,
    }),
  })
  const currentChatId = boardSearch?.currentChatId
  const isResearchHandoff = Boolean(boardSearch?.research)
  const urlDraft = boardSearch?.draft

  const [showResearchBanner, setShowResearchBanner] = useState(false)
  const [draftSeed, setDraftSeed] = useState<string | null>(null)
  const [bannerNonce, setBannerNonce] = useState(0)

  useEffect(() => {
    const fromFlag = consumeResearchHandoffFlag()
    const fromStorage = consumeResearchDraft()
    // Prefer URL draft (works cross-origin) then sessionStorage.
    let fromUrl: string | null = null
    if (urlDraft) {
      try {
        fromUrl = decodeURIComponent(urlDraft)
      } catch {
        fromUrl = urlDraft
      }
    }
    const draft = fromStorage || fromUrl
    if (draft) setDraftSeed(draft)
    if (isResearchHandoff || fromFlag || draft) {
      setShowResearchBanner(true)
      // Open full sheet so research pills + history are visible.
      setChatSheetOpen(true)
      setBannerNonce((n) => n + 1)
    }
  }, [isResearchHandoff, setChatSheetOpen, boardId, urlDraft])

  return (
    <div className="absolute inset-0 h-full w-full overflow-hidden">
      <div className="relative h-full w-full bg-background">
        <HarnessCanvas />
        {showResearchBanner && !presentationMode && (
          <ResearchHandoffBanner
            key={bannerNonce}
            onPick={(prompt) => {
              setDraftSeed(prompt)
              setChatSheetOpen(true)
            }}
            onDismiss={() => setShowResearchBanner(false)}
          />
        )}
        {!chatSheetOpen && !presentationMode && boardId && (
          <FloatingAssistant
            boardId={boardId}
            currentChatId={currentChatId}
            onOpenFullSheet={() => setChatSheetOpen(true)}
            initialDraft={draftSeed}
            onDraftConsumed={() => setDraftSeed(null)}
          />
        )}
        <CopilotSheet
          open={chatSheetOpen}
          onOpenChange={setChatSheetOpen}
          boardId={boardId ?? undefined}
          currentChatId={currentChatId}
          initialDraft={draftSeed}
          onDraftConsumed={() => setDraftSeed(null)}
          onOpenFullChat={(chatId) => {
            setChatSheetOpen(false)
            if (chatId) {
              navigate({
                to: "/chats/$id",
                params: { id: chatId },
                search: (prev: Record<string, unknown>) => ({
                  ...prev,
                  board_id: boardId || undefined,
                }),
              })
            }
          }}
        />
      </div>
    </div>
  )
}
