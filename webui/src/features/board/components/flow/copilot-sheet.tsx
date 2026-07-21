import { Button } from '@/components/ui/button'
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { Chat } from '@/features/agent/components/chat-view'
import { ArrowExpandIcon, ExternalLinkIcon, SparklesIcon } from '@/components/icons'

interface CopilotSheetProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  boardId?: string
  currentChatId?: string
  onOpenFullChat: (chatId?: string) => void
  /** Prefill chat composer (research handoff). */
  initialDraft?: string | null
  onDraftConsumed?: () => void
}

/**
 * Reusable board copilot sheet that can be mounted from any board surface.
 * It stays non-modal so users can keep interacting with the graph while open.
 */
export function CopilotSheet({
  open,
  onOpenChange,
  boardId,
  currentChatId,
  onOpenFullChat,
  initialDraft = null,
  onDraftConsumed,
}: CopilotSheetProps) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange} modal={false} disablePointerDismissal>
      <SheetContent
        side="right"
        showOverlay={false}
        showClose={false}
        className="md:w-[500px] md:max-w-[92vw] w-full bg-background text-sidebar-foreground border-l-1 border-border/70 p-0 shadow-sm z-[60]"
      >
        {/* Radix Dialog primitive requires an accessible title for content. */}
        <SheetHeader className="sr-only">
          <SheetTitle>Board Assistant</SheetTitle>
        </SheetHeader>
        <div className="px-4 py-3 flex items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-sm font-medium">
            <div className="flex items-center justify-center rounded-md bg-gradient-to-br from-wiki-link to-secondary-foreground size-6 shadow-sm">
              <SparklesIcon className="size-3.5 text-primary-foreground" weight="fill" />
            </div>
            Board Assistant
          </div>
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="icon"
              onClick={() => onOpenFullChat(currentChatId)}
              title="Open full chat view"
              aria-label="Open full chat view"
            >
              <ExternalLinkIcon className="size-4" strokeWidth={2} />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => onOpenChange(false)}
              title="Close"
              aria-label="Close"
            >
              <ArrowExpandIcon className="size-4" strokeWidth={2} />
            </Button>
          </div>
        </div>
        <div className="flex-1 relative overflow-y-auto scrollbar-thin">
          {/* Lazy-mount the chat tree only while the sheet is actually open.
             This keeps the heavy Conversation + message-history mount cost
             from lingering across open/close cycles. */}
          {open && boardId ? (
            <Chat
              initialBoardId={boardId}
              className="relative"
              showHistoricalChats
              chatId={currentChatId}
              enableSelectionContext
              initialDraft={initialDraft}
              onDraftConsumed={onDraftConsumed}
            />
          ) : open ? (
            <div className="w-full h-full flex items-center justify-center text-sm text-muted-foreground">
              Select a board to start a conversation.
            </div>
          ) : null}
        </div>
      </SheetContent>
    </Sheet>
  )
}
