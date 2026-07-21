import { useEffect, useState, type KeyboardEvent } from "react"
import TextareaAutosize from "react-textarea-autosize"
import { toast } from "sonner"
import { SparklesIcon } from "@/components/icons"
import { ThinkingIndicator } from "@/components/animations/thinking-indicator"
import { cn } from "@/lib/utils"
import { SendMessageError } from "@/features/agent/api/send-message"
import { useSubmitPrompt } from "@/features/agent/hooks/use-submit-prompt"
import {
  buildMessageContext,
  useHasMessageContext,
  useSelectionContextSummary,
} from "@/features/agent/hooks/use-message-context"
import { ProgressLine } from "./progress-line"
import { useCurrentAssistantMessage } from "./use-current-assistant-message"
import { useBoardAppStore } from "../../../harness/store/board-app-store"
import { getResearchDraft } from "../../../lib/research-handoff"


export interface FloatingIslandProps {
  boardId: string
  onOpenFullSheet: () => void
  initialDraft?: string | null
  onDraftConsumed?: () => void
}


/**
 * Floating composer pill anchored at the bottom-center of the board.
 * Shares the active board chat via useSubmitPrompt; errors surface as toasts.
 * Supports research handoff draft + selection-aware chip/quick actions.
 */
export const FloatingIsland = ({
  boardId,
  onOpenFullSheet,
  initialDraft = null,
  onDraftConsumed,
}: FloatingIslandProps) => {
  const [input, setInput] = useState("")
  const latestAssistantMessage = useCurrentAssistantMessage()
  const isStreaming = latestAssistantMessage?.streaming === true
  const submit = useSubmitPrompt()
  const hasMessageContext = useHasMessageContext()
  const selection = useSelectionContextSummary({ enabled: true })
  // Single boolean derivation — only re-renders when a surface opens or
  // closes, never when its content/title changes. Cheap.
  const hasActiveSurface = useBoardAppStore((s) => Boolean(s.activeNodeSurface))

  useEffect(() => {
    if (!initialDraft) return
    setInput(initialDraft)
    onDraftConsumed?.()
  }, [initialDraft, onDraftConsumed])

  const handleSubmit = async () => {
    if (isStreaming) return
    const trimmed = input.trim()
    if (!trimmed) return
    setInput("")
    try {
      const messageContext = buildMessageContext()
      await submit(trimmed, { attachedBoardId: boardId, messageContext })
    } catch (error) {
      const message = error instanceof SendMessageError
        ? error.message
        : error instanceof Error
          ? error.message
          : "Could not send message."
      toast.error(message)
    }
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      void handleSubmit()
    }
  }

  const contextLabel = hasActiveSurface
    ? "@page"
    : selection.count > 0
      ? selection.count === 1
        ? `@sel: ${(selection.title || "node").slice(0, 18)}`
        : `@sel (${selection.count})`
      : "@board"

  const quick =
    selection.count > 0
      ? [
        { label: "Đào sâu", draft: getResearchDraft("focus") },
        { label: "Chỗ thiếu", draft: getResearchDraft("gaps") },
      ]
      : [
        { label: "Tóm tắt", draft: getResearchDraft("summary") },
        { label: "Gap", draft: getResearchDraft("gaps") },
      ]

  return (
    <div data-coachmark='ai-island' className='absolute bottom-1 left-1/2 -translate-x-1/2 z-[60] w-[min(580px,calc(100vw-4rem))] pointer-events-auto hidden md:flex flex-col gap-1.5'>
      <div className='flex flex-wrap justify-center gap-1.5 px-1'>
        {quick.map((q) => (
          <button
            key={q.label}
            type='button'
            disabled={isStreaming}
            onClick={() => setInput(q.draft)}
            className='rounded-full border border-border/70 bg-sidebar/80 backdrop-blur px-2.5 py-0.5 text-[11px] font-medium text-card-foreground/80 hover:bg-sidebar disabled:opacity-50'
          >
            {q.label}
          </button>
        ))}
        <button
          type='button'
          onClick={onOpenFullSheet}
          className='rounded-full border border-border/70 bg-sidebar/80 backdrop-blur px-2.5 py-0.5 text-[11px] font-medium text-muted-foreground hover:text-foreground'
        >
          Mở chat đầy đủ
        </button>
      </div>
      <div className={cn(
        "bg-sidebar/80 backdrop-blur-md backdrop-saturate-150",
        "border rounded-2xl flex flex-col",
        "shadow-[0_12px_32px_-4px_rgba(0,0,0,0.28),0_2px_8px_-2px_rgba(0,0,0,0.12)]",
        "dark:shadow-[0_16px_36px_-4px_rgba(0,0,0,0.55),0_2px_8px_-2px_rgba(0,0,0,0.3)]",
        "transition-[box-shadow,border-color] focus-within:border-secondary-foreground/50",
        "focus-within:ring-4 focus-within:ring-secondary-foreground/20",
        isStreaming ? "border-secondary-foreground/50 animate-ring-pulse-soft" : "border-border",
      )}>
        <ProgressLine />
        <div className='flex items-center gap-2 p-3'>
          {isStreaming ? (
            <button
              type='button'
              onClick={onOpenFullSheet}
              title='Open full chat'
              aria-label='Open full chat'
              className='inline-flex items-center shrink-0 rounded-md cursor-pointer transition hover:opacity-80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-secondary-foreground/30'
            >
              <ThinkingIndicator className='text-xs text-foreground/70' iconSize={14} />
            </button>
          ) : (
            <button
              type='button'
              onClick={onOpenFullSheet}
              title='Open full chat'
              aria-label='Open full chat'
              className='flex items-center justify-center rounded-md bg-gradient-to-br from-wiki-link to-secondary-foreground size-7 shrink-0 shadow-sm cursor-pointer transition hover:brightness-110 focus-visible:outline-none'
            >
              <SparklesIcon className='size-3.5 text-primary-foreground' weight='fill' />
            </button>
          )}
          <span
            className={cn(
              "text-xs font-mono px-2 py-0.5 rounded shrink-0 max-w-[10rem] truncate",
              selection.count > 0 || hasActiveSurface
                ? "bg-primary/15 text-primary border border-primary/20"
                : "bg-secondary text-secondary-foreground",
            )}
            title={
              hasMessageContext
                ? "Selected canvas context will be sent"
                : "Board-level context"
            }
          >
            {contextLabel}
          </span>
          <TextareaAutosize
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={
              selection.count > 0
                ? "Hỏi về ô đang chọn…"
                : "Hỏi về board — hoặc click 1 ô…"
            }
            minRows={1}
            maxRows={4}
            disabled={isStreaming}
            className='flex-1 min-w-0 bg-transparent text-sm outline-none resize-none py-1 placeholder:text-muted-foreground scrollbar-thin'
          />
          <span className='shrink-0 text-sm text-muted-foreground/70 font-mono px-1 select-none hidden sm:inline'>
            ⌘↵
          </span>
        </div>
      </div>
      <p className='text-center text-[11px] text-muted-foreground/70 px-3'>
        Click ô → @selection · gợi ý phía trên · graph là bộ nhớ research
      </p>
    </div>
  )
}
