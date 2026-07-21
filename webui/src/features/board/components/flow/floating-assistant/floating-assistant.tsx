import { ChatProvider } from "@/features/agent/hooks/chat-context"
import { AnswerCard } from "./answer-card"
import { FloatingIsland } from "./floating-island"


export interface FloatingAssistantProps {
  boardId: string
  currentChatId?: string
  onOpenFullSheet: () => void
  initialDraft?: string | null
  onDraftConsumed?: () => void
}


/**
 * Ambient board assistant: a composer pill at the bottom of the canvas, an
 * adaptive progress strip, and a right-side answer card that surfaces text
 * replies. Shares the chat session with the full CopilotSheet via the
 * `current_chat_id` search param.
 */
export const FloatingAssistant = ({
  boardId,
  currentChatId,
  onOpenFullSheet,
  initialDraft = null,
  onDraftConsumed,
}: FloatingAssistantProps) => {
  return (
    <ChatProvider initialChatId={currentChatId}>
      <FloatingIsland
        boardId={boardId}
        onOpenFullSheet={onOpenFullSheet}
        initialDraft={initialDraft}
        onDraftConsumed={onDraftConsumed}
      />
      <AnswerCard onOpenFullSheet={onOpenFullSheet} />
    </ChatProvider>
  )
}
