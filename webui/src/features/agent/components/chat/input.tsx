import { useEffect, useState, type KeyboardEvent } from 'react'
import clsx from 'clsx'
import { useChatStore } from '../../store/chat-store'
import { SendMessageError } from '../../api/send-message'
import { useSubmitPrompt } from '../../hooks/use-submit-prompt'
import {
  buildMessageContext,
  useHasMessageContext,
  useSelectionContextSummary,
} from '../../hooks/use-message-context'
import { useAppStore } from '@/store'
import type { BillingPlan } from '@/lib/decode-jwt'
import { SendButton } from './send-button'
import TextareaAutosize from 'react-textarea-autosize'
import { useChat } from '../../hooks/chat-context'
import { useBoardAppStore } from '@/features/board/harness/store/board-app-store'
import { useNavigate, useParams } from '@tanstack/react-router'
import { SettingsBillingUrl } from '@/routes'
import { WelcomeMessage } from './welcome-message'
import { StarterPromptPills } from './starter-prompts'
import { InputSettings } from './input-settings/settings'
import { useIsBoardCreationLimited, FREE_PLAN_BOARD_LIMIT_TOOLTIP } from '@/features/board/lib/board-limit'

// shadcn/ui
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { AlertIcon } from '@/components/icons'
import { toast } from 'sonner'

export interface InputBarProps {
  attachedBoardId?: string
  layout?: "floating" | "docked"
  preferChatRoute?: boolean
  enableSelectionContext?: boolean
  /** When true (e.g. on home), submitting creates a fresh board and routes to it. */
  autoCreateBoard?: boolean
  /** One-shot prefill from research handoff. */
  initialDraft?: string | null
  onDraftConsumed?: () => void
}


/**
 * Formats a Retry-After duration into a short user-facing hint.
 */
const formatRetryAfter = (retryAfter?: number) => {
  if (!retryAfter || retryAfter <= 0) return null
  if (retryAfter < 60) return `Try again in ${retryAfter}s.`
  const minutes = Math.ceil(retryAfter / 60)
  return `Try again in ${minutes} min.`
}

/**
 * Builds a friendlier quota description for long-lived limit toasts.
 */
const buildLimitDescription = ({
  userPlan,
  retryAfter,
}: {
  userPlan: BillingPlan
  retryAfter?: number
}) => {
  const resetHint = retryAfter && retryAfter >= 60 * 60 * 8
    ? "It should reset automatically tomorrow."
    : retryAfter && retryAfter >= 60 * 60
      ? "It should reset automatically later today."
      : formatRetryAfter(retryAfter) ?? "It should reset automatically soon."

  if (userPlan === "free") {
    return `We’re a small indie project running on a very tight budget, so the free tier is capped for now. ${resetHint} If you need more room, please consider self-hosting or upgrading to Plus.`
  }

  return `We’re a small indie project running on a very tight budget, so usage is still capped for now. ${resetHint} If you need more room, you can also self-host or review the available plans.`
}

/**
 * Input bar with Deep Research confirmation using ONLY `input` state.
 * If `useDeepResearch` is enabled, pressing Enter/Send opens a dialog that:
 *  - Explains it will create a NEW chat
 *  - Lets the user edit the SAME input
 *  - Confirms to send & create a new chat
 */
export const InputBar = ({
  attachedBoardId,
  layout = "floating",
  preferChatRoute = false,
  enableSelectionContext = false,
  autoCreateBoard = false,
  initialDraft = null,
  onDraftConsumed,
}: InputBarProps) => {
  const { chatId } = useChat()

  const userPlan = useAppStore((state) => state.userPlan)

  const isStreaming = useChatStore((state) => state.isStreaming)
  const useDeepResearch = useChatStore((state) => state.useDeepResearch)

  const [input, setInput] = useState<string>('')

  // Apply research handoff draft once when parent passes it.
  useEffect(() => {
    if (!initialDraft) return
    setInput(initialDraft)
    onDraftConsumed?.()
  }, [initialDraft, onDraftConsumed])

  // Deep Research dialog state
  const [showDRDialog, setShowDRDialog] = useState(false)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [limitDialogCopy, setLimitDialogCopy] = useState<{
    title: string
    description: string
  } | null>(null)

  const submit = useSubmitPrompt()
  const navigate = useNavigate()
  const boardParams = useParams({ from: "/boards/$id", shouldThrow: false })
  const boardRouteId = boardParams?.id
  const settingsBoardId = attachedBoardId ?? boardRouteId
  const memorySearchAvailable = Boolean(settingsBoardId)

  const isBoardCreationLimited = useIsBoardCreationLimited()
  // Only the home composer (autoCreateBoard) is gated by the limit; existing
  // boards can still receive new chats even when the user is at the cap.
  const showBoardLimitGate = autoCreateBoard && isBoardCreationLimited
  const showBoardChip = autoCreateBoard || Boolean(attachedBoardId)
  // Single boolean: re-renders only when the dialog opens or closes,
  // not on title/content changes. Cheap.
  const hasActiveSurface = useBoardAppStore((s) => Boolean(s.activeNodeSurface))
  const selectionSummary = useSelectionContextSummary({
    enabled: enableSelectionContext,
  })
  const hasSelectionContext = useHasMessageContext({
    enabled: enableSelectionContext,
  })
  const placeholder = showBoardLimitGate
    ? "You've reached your plan's board limit"
    : hasActiveSurface
      ? "Hỏi về page này…"
      : selectionSummary.count > 0
        ? "Hỏi tiếp về ô đang chọn (Enter gửi)…"
        : enableSelectionContext && attachedBoardId
          ? "Hỏi về cả board — hoặc click 1 ô rồi hỏi riêng…"
          : autoCreateBoard
            ? "Start a new board with a question…"
            : "Ask anything..."

  const proceedSend = async (text: string, forceNewChat = false) => {
    const trimmed = text.trim()
    if (!trimmed) return

    setInput('')

    try {
      await submit(trimmed, {
        forceNewChat,
        attachedBoardId,
        preferChatRoute,
        messageContext: buildMessageContext({ enabled: enableSelectionContext }),
        autoCreateBoard,
      })
    } catch (error) {
      if (error instanceof SendMessageError && error.status === 429) {
        setLimitDialogCopy({
          title: "You’ve reached your AI request limit for now.",
          description: buildLimitDescription({ userPlan, retryAfter: error.retryAfter }),
        })
      } else {
        const message = error instanceof Error ? error.message : "Could not send message."
        toast.error(message)
      }
      throw error
    }
  }

  const handlePrimarySend = async () => {
    if (isStreaming) return
    if (useDeepResearch) {
      setShowDRDialog(true)
      return
    }
    await proceedSend(input, false)
  }

  /**
   * Send a predefined starter prompt through the normal first-message flow.
   */
  const handleStarterPromptSelect = async (prompt: string) => {
    if (isStreaming) return
    if (useDeepResearch) {
      setInput(prompt)
      setShowDRDialog(true)
      return
    }
    await proceedSend(prompt, false)
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void handlePrimarySend()
    }
  }

  const confirmDeepResearch = async () => {
    const trimmed = input.trim()
    if (!trimmed) return
    try {
      setIsSubmitting(true)
      setShowDRDialog(false)
      await proceedSend(trimmed, true /* force new chat */)
    } finally {
      setIsSubmitting(false)
    }
  }

  const commandIconClass = clsx(
    'ml-auto !p-2 !size-8',
    isStreaming ? 'cursor-not-allowed' : 'cursor-pointer'
  )

  const isFloating = layout === "floating"

  const className = clsx(
    'transition-all flex flex-col items-center bg-transparent',
    isFloating
      ? clsx(
        'absolute inset-x-0 p-2 pb-0 sm:pb-0 sm:p-4 z-20 gap-12',
        chatId ? 'bottom-0' : 'bottom-1/2 transform translate-y-1/2'
      )
      : clsx(
        'w-full gap-4 px-4 pb-4',
        chatId ? 'mt-auto' : 'flex-1 justify-center'
      )
  )

  const inboxClass = clsx(
    'rounded-2xl relative flex flex-col text-card-foreground text-base p-3 gap-2 border transition-[box-shadow,border-color,opacity]',
    'bg-card backdrop-blur-md backdrop-saturate-150 supports-[backdrop-filter]:bg-card/70 shadow-md',
    'border-border hover:border-secondary-foreground/50',
    'focus-within:border-secondary-foreground/50 focus-within:ring-4 focus-within:ring-secondary-foreground/10',
    showBoardLimitGate && 'opacity-60 hover:border-border focus-within:border-border focus-within:ring-0',
  )
  const showStarterPrompts = !chatId && !isStreaming && Boolean(attachedBoardId) && !showBoardLimitGate
  // Research quick-actions: always when on a board with selection context enabled
  // (launcher-built graphs). Selection-specific pills when a node is selected.
  const showResearchPills =
    enableSelectionContext &&
    !isStreaming &&
    !showBoardLimitGate &&
    Boolean(attachedBoardId)

  const researchPills = selectionSummary.count > 0
    ? [
      {
        id: "expand-selected",
        label: "Đào sâu ô này",
        prompt:
          "Chỉ làm việc với (các) node đang chọn trong message context. " +
          "Làm rõ / đào sâu nhánh này: thêm Source/Evidence/Finding con + edge về focus. " +
          "Brand/campaign/message cụ thể + URL nếu có. Không rewrite nhánh khác. " +
          "Claim thiếu evidence → Unknown hoặc confidence thấp.",
      },
      {
        id: "clarify-selected",
        label: "Làm rõ bài toán",
        prompt:
          "Dựa trên node đang chọn: viết lại vấn đề đang nghiên cứu cho dễ hiểu, " +
          "liệt kê 3 câu hỏi hẹp hơn, và đề xuất bước tiếp theo trên board " +
          "(có thể tạo Finding/Unknown). Không xóa taxonomy hiện có.",
      },
      {
        id: "gaps-selected",
        label: "Chỗ còn thiếu",
        prompt:
          "Trong phạm vi node đang chọn và lân cận: claim nào thiếu Source? " +
          "Thêm Unknown/Contradiction/Finding nếu cần. Không đụng nhánh khác.",
      },
    ]
    : [
      {
        id: "summarize-board",
        label: "Tóm tắt board",
        prompt:
          "Đọc research graph trên board. Tóm tắt: câu hỏi chính, các workstream/mode, " +
          "5 insight quan trọng, chỗ evidence còn yếu. Ngắn, dễ hiểu.",
      },
      {
        id: "find-gaps",
        label: "Tìm gap",
        prompt:
          "Rà soát board: claim thiếu Source, nhánh trống, mâu thuẫn. " +
          "Thêm Unknown/Contradiction/Finding về gap. Không đổi taxonomy tổng thể.",
      },
      {
        id: "next-questions",
        label: "Câu hỏi tiếp",
        prompt:
          "Từ graph hiện tại, đề xuất 3–5 câu hỏi research tiếp theo (hẹp, actionable) " +
          "và ghi gợi ý lên board (Finding hoặc Question con) nếu phù hợp.",
      },
    ]

  const contextChipLabel = hasActiveSurface
    ? "@page"
    : selectionSummary.count > 0
      ? selectionSummary.count === 1
        ? `@selection: ${selectionSummary.title || "node"}`
        : `@selection (${selectionSummary.count}): ${selectionSummary.title || "nodes"}`
      : "@board"

  const inboxBody = (
    <div className={inboxClass}>
      <div className="flex items-start gap-2 p-0">
        {showBoardChip && (
          <span
            className={clsx(
              "mt-1 shrink-0 rounded px-2 py-0.5 font-mono text-xs max-w-[14rem] truncate",
              selectionSummary.count > 0 || hasActiveSurface
                ? "bg-primary/15 text-primary border border-primary/25"
                : "bg-secondary text-secondary-foreground",
            )}
            title={
              hasSelectionContext
                ? "Selected canvas context will be sent with your message"
                : "Board-level context"
            }
          >
            {contextChipLabel}
          </span>
        )}
        <TextareaAutosize
          onKeyDown={handleKeyDown}
          onChange={(e) => setInput(e.target.value)}
          value={input}
          minRows={1}
          maxRows={15}
          placeholder={placeholder}
          disabled={showBoardLimitGate}
          className="flex-1 min-w-0 resize-none border-none outline-none bg-transparent text-base disabled:cursor-not-allowed"
          autoFocus
        />
      </div>

      {showResearchPills && (
        <div className="flex flex-wrap items-center gap-1.5 px-0.5">
          <span className="text-[10px] uppercase tracking-wide text-muted-foreground/80 shrink-0">
            {selectionSummary.count > 0 ? "Ô đang chọn" : "Research"}
          </span>
          {researchPills.map((p) => (
            <button
              key={p.id}
              type="button"
              disabled={isStreaming}
              onClick={() => void handleStarterPromptSelect(p.prompt)}
              className="rounded-md border border-border/70 bg-background/60 px-2 py-1 text-xs font-medium text-card-foreground/80 hover:bg-sidebar disabled:opacity-50"
            >
              {p.label}
            </button>
          ))}
        </div>
      )}

      <div className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 flex-wrap items-center gap-1">
          <InputSettings
            showBoardContextOption={enableSelectionContext}
            memorySearchAvailable={memorySearchAvailable}
          />
        </div>

        <div className="flex items-center gap-2">
          <span className="hidden select-none px-1 font-mono text-sm text-muted-foreground/70 sm:inline">
            ⌘↵
          </span>
          <SendButton
            loadingStatus={isStreaming ? 'loading' : 'loaded'}
            disabled={isStreaming || showBoardLimitGate}
            onClick={handlePrimarySend}
            className={commandIconClass}
          />
        </div>
      </div>
    </div>
  )

  return (
    <div className={className}>
      {!chatId && (
        <WelcomeMessage
          afterContent={showStarterPrompts ? <StarterPromptPills onSelect={handleStarterPromptSelect} /> : undefined}
        />
      )}

      <div className={clsx(
        "flex flex-col space-y-2 w-full items-center justify-center",
        isFloating ? '' : 'max-w-[900px] mx-auto'
      )}>
        <div className="relative w-full max-w-[800px] mx-auto">
          {showBoardLimitGate ? (
            <Tooltip delayDuration={200}>
              <TooltipTrigger asChild>{inboxBody}</TooltipTrigger>
              <TooltipContent className="max-w-xs text-center">
                {FREE_PLAN_BOARD_LIMIT_TOOLTIP}
              </TooltipContent>
            </Tooltip>
          ) : inboxBody}

          <p className="p-1.5 sm:p-2 text-center text-[11px] text-muted-foreground/80 bg-auto">
            AI can make mistakes. Verify important details carefully.
          </p>
        </div>
      </div>

      {/* Deep Research Confirmation Dialog (uses the SAME `input`) */}
      <Dialog open={showDRDialog} onOpenChange={setShowDRDialog}>
        <DialogContent className="sm:max-w-[560px]">
          <DialogHeader>
            <DialogTitle>Start a Deep Research in a new chat?</DialogTitle>
            <DialogDescription>
              Deep Research runs longer, may use more tools, and will be created in a <strong>separate chat</strong>. Edit your prompt below before starting.
            </DialogDescription>
          </DialogHeader>

          <div className="grid w-full gap-2 py-2">
            <Label htmlFor="dr-prompt">Your prompt</Label>
            <TextareaAutosize
              id="dr-prompt"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              minRows={4}
              maxRows={18}
              className="w-full resize-none rounded-md border border-border/50 shadow-sm bg-background px-3 py-2 text-base outline-none"
              placeholder="Refine your prompt here..."
              autoFocus
            />
          </div>

          <DialogFooter className="gap-2 sm:gap-3">
            <Button variant="ghost" onClick={() => setShowDRDialog(false)} disabled={isSubmitting}>
              Cancel
            </Button>
            <Button onClick={confirmDeepResearch} disabled={isSubmitting || !input.trim()}>
              {isSubmitting ? 'Starting…' : 'Start Deep Research'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={limitDialogCopy !== null} onOpenChange={(open) => {
        if (!open) setLimitDialogCopy(null)
      }}>
        <DialogContent className="sm:max-w-[560px]">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertIcon className="size-5 shrink-0 text-secondary-foreground" strokeWidth={2} />
              <span>{limitDialogCopy?.title}</span>
            </DialogTitle>
            <DialogDescription className="text-sm leading-7 text-foreground/80">
              {limitDialogCopy?.description}
            </DialogDescription>
          </DialogHeader>

          <DialogFooter className="gap-2 sm:gap-3">
            <Button variant="ghost" onClick={() => setLimitDialogCopy(null)}>
              Close
            </Button>
            <Button
              onClick={() => {
                setLimitDialogCopy(null)
                navigate({ to: SettingsBillingUrl })
              }}
            >
              Upgrade
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
