import { API_URL } from "@/config/api"
import type { AgentResponse, ReasoningStep } from "../types/stream"
import type { SendMessageRequestPayload } from "./types"
import { handleStreamingResponse } from "../utils/stream/digest"
import { useChatStore } from "../store/chat-store"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useNavigate, useRouter } from "@tanstack/react-router"
import type { ChatMessage } from "../types/chat"
import snakecaseKeys from "snakecase-keys"
import { buildResponse } from "../utils/stream/build"
import { fetchWithAuthRaw } from "@/api"
import type { ToolOutput } from "../types/tool-outputs"
import { trimResponseAnnotations } from "../utils/annotations"
import { useBoardAppStore } from "@/features/board/harness/store/board-app-store"
import { getAgentBridge } from "@/features/board/harness/agent/agent-bridge"
import type {
  ChangeNoteKindOutput,
  CreateNoteOutput,
  DeleteSubtreeOutput,
  EditNoteOutput,
  LinkNotesOutput,
  MergeNotesOutput,
  RelayoutOutput,
  ReparentNoteOutput,
  SplitNoteOutput,
  WriteNoteOutput,
} from "../types/tool-outputs"
import { isReasoningTextStep, isToolCallStep, normalizeReasoningSteps } from "../types/stream"

export class SendMessageError extends Error {
  status: number
  retryAfter?: number

  /**
   * Represents a failed streaming chat send with optional retry metadata.
   */
  constructor(message: string, status: number, retryAfter?: number) {
    super(message)
    this.name = "SendMessageError"
    this.status = status
    this.retryAfter = retryAfter
  }
}


/**
 * Reads a failed streaming response into a user-facing error.
 */
async function readSendMessageError(response: Response): Promise<SendMessageError> {
  const retryAfterHeader = response.headers.get("Retry-After")
  const retryAfter = retryAfterHeader ? Number(retryAfterHeader) : undefined

  let message = `sendMessage failed: ${response.status} ${response.statusText}`
  try {
    const text = await response.text()
    if (text) {
      try {
        const parsed = JSON.parse(text) as { detail?: string, data?: { message?: string } }
        message = parsed.detail || parsed.data?.message || text
      } catch {
        message = text
      }
    }
  } catch {
    // Keep the default fallback when the body cannot be read.
  }

  return new SendMessageError(message, response.status, Number.isFinite(retryAfter) ? retryAfter : undefined)
}


/**
 * Send a message to the AI assistant.
 *
 * @param payload - The message payload to send.
 * @param chatId - The ID of the chat to send the message to.
 *
 * @returns An async generator that yields the streamed response messages.
 */
export async function* sendMessage(
  payload: SendMessageRequestPayload,
  chatId: string,
  userId: string,
  opts?: { signal?: AbortSignal }
): AsyncGenerator<Record<string, unknown>> {
  const url = new URL(`/chats/${chatId}/messages`, API_URL)
  url.searchParams.set("user_id", userId)

  // Build headers without Authorization; fetchWithAuthRaw adds it
  const headers = new Headers()
  headers.set("Content-Type", "application/json")

  const body = JSON.stringify(
    snakecaseKeys(payload as unknown as Record<string, unknown>, { deep: true })
  )

  const response = await fetchWithAuthRaw(url.toString(), {
    method: "POST",
    headers,
    body,
    cache: "no-store",
    keepalive: false,
    signal: opts?.signal,
  })

  if (!response.ok) {
    throw await readSendMessageError(response)
  }

  // hand off to your streaming parser (SSE/NDJSON/etc.)
  yield* handleStreamingResponse<Record<string, unknown>>(response)
}


/**
 * Custom hook to send a message to the AI assistant.
 *
 * @returns An object containing the sendMessage function and its state.
 */
export const useSendMessage = () => {
  const setIsStreaming = useChatStore((state) => state.setIsStreaming)

  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const router = useRouter()

  const mutation = useMutation({
    mutationFn: async ({
      payload,
      userId,
      chatId,
    }: {
      payload: SendMessageRequestPayload,
      userId: string,
      chatId: string
    }) => {
      const key = ['listMessages', chatId, userId] as const
      setIsStreaming(true)

      await queryClient.cancelQueries({ queryKey: key, exact: true })

      // Optimistically update the chat messages in the query cache
      const newUserMessage = {
        id: payload.messageId,
        role: "user",
        content: { markdown: payload.query },
        chatUid: chatId,
        properties: payload.messageContext
          ? {
            context: {
              type: "text",
              text: payload.messageContext
            }
          }
          : {}
      } as ChatMessage

      const tmpId = "placeholder-" + Date.now().toString()

      const isDeepResearch = payload.useDeepResearch
      const newAssistantPlaceholder = {
        id: tmpId,
        role: "assistant",
        content: { markdown: "" },
        chatUid: chatId,
        properties: { reasoning: { type: "reasoning", reasoning: [] } },
        streaming: true,
        isDeepResearch,
        sentAt: new Date().toISOString()
      } as ChatMessage

      const newMessages = [newUserMessage, newAssistantPlaceholder]
      try {
        queryClient.setQueryData<ChatMessage[]>(
          key,
          (oldMessages) => [
            ...(oldMessages || []),
            ...newMessages
          ]
        )
        const stream = sendMessage(payload, chatId, userId)
        const response = buildResponse(stream)
        let setNewAssistantMessageId = false

        for await (const resp of response) {
          const { response: rep, isStop } = resp
          if (rep.steps.length === 0) {
            continue
          }

          const safeResponse = trimResponseAnnotations(
            sanitizeResponseForStreaming(rep, isStop)
          )

          if (!setNewAssistantMessageId) {
            setNewAssistantMessageId = true

            queryClient.setQueryData<ChatMessage[]>(
              key,
              (oldMessages) => {
                let msgs = oldMessages || []
                if (!oldMessages || oldMessages.length === 0) {
                  msgs = [...newMessages]
                }
                return msgs
              }
            )
          } else {
            queryClient.setQueryData<ChatMessage[]>(
              ["listMessages", chatId, userId],
              (oldMessages) => {
                let msgs = oldMessages || []
                if (!oldMessages || oldMessages.length === 0) {
                  msgs = [...newMessages]
                }
                return msgs.map((m) => {
                  const content = safeResponse.steps
                    .filter(isReasoningTextStep)
                    .map((step) => step.message)
                    .join("")
                  if (m.id === tmpId) {
                    return {
                      ...m,
                      content: { markdown: content },
                      properties: {
                        ...m.properties,
                        reasoning: {
                          type: "reasoning",
                          reasoning: safeResponse.steps
                        }
                      },
                      streaming: !isStop
                    } as ChatMessage
                  }
                  return m
                })
              }
            )
          }
        }
      } catch (error) {
        console.error("Error sending message:", error)
        throw error
      } finally {
        setIsStreaming(false)
        await queryClient.invalidateQueries({ queryKey: key, exact: true })
        const messages = queryClient.getQueryData<ChatMessage[]>(key) ?? []

        const userMessageIndex = messages.findIndex(
          (message) => message.id === payload.messageId && message.role === "user",
        )
        const completedMessage = userMessageIndex >= 0
          ? messages.slice(userMessageIndex + 1).find((message) => message.role === "assistant")
          : undefined

        const reasoningSteps = completedMessage?.properties.reasoning?.reasoning ?? []
        const noteToolOutputs = collectNoteToolOutputs(reasoningSteps)
        const linkToolOutputs = collectLinkToolOutputs(reasoningSteps)
        const structToolOutputs = collectStructToolOutputs(reasoningSteps)

        if (
          noteToolOutputs.length > 0
          || linkToolOutputs.length > 0
          || structToolOutputs.length > 0
        ) {
          const activeBoardId = useBoardAppStore.getState().boardId
          // Apply outputs through the canvas-harness bridge: re-fetches
          // each note/link from the server (canonical state), updates
          // the React Query cache so any open sub-page panels see the
          // edit, and writes a `remote`-origin batch into the harness
          // store so the canvas reflects the change without triggering
          // the debounced save loop.
          const harnessBridge = getAgentBridge()

          if (activeBoardId && harnessBridge) {
            const createdNoteIds: string[] = []
            for (const output of noteToolOutputs) {
              const result = await harnessBridge.applyNoteOutput(output)
              if (result?.created && result.onCanvas) {
                createdNoteIds.push(result.noteId)
              }
            }
            for (const output of linkToolOutputs) {
              await harnessBridge.applyLinkOutput(output)
            }
            // Apply the six structural tool outputs. Destructive ops
            // (delete_subtree / relayout_board) are no-ops here — the
            // collab WS path delivers those mutations to all clients.
            for (const output of structToolOutputs) {
              switch (output.type) {
                case "change_note_kind":
                  await harnessBridge.applyChangeKindOutput(output)
                  break
                case "reparent_note":
                  await harnessBridge.applyReparentNoteOutput(output)
                  break
                case "delete_subtree":
                  await harnessBridge.applyDeleteSubtreeOutput(output)
                  break
                case "merge_notes":
                  await harnessBridge.applyMergeNotesOutput(output)
                  break
                case "split_note": {
                  const result = await harnessBridge.applySplitNoteOutput(output)
                  if (result?.created && result.onCanvas) {
                    createdNoteIds.push(result.noteId)
                  }
                  break
                }
                case "relayout_board":
                  await harnessBridge.applyRelayoutOutput(output)
                  break
              }
            }

            if (
              createdNoteIds.length > 0
              && router.state.location.pathname.startsWith(`/boards/${activeBoardId}`)
            ) {
              const centerIds = createdNoteIds.join(",")
              navigate({
                to: "/boards/$id",
                params: { id: activeBoardId },
                replace: true,
                search: (prev: Record<string, unknown>) => ({ ...prev, center: centerIds }),
              })
            }
          }
        }
      }
    }
  })
  return {
    sendMessage: mutation.mutate,
    sendMessageAsync: mutation.mutateAsync,
    ...mutation
  }
}

const STREAMING_EVENT_CAP = 10

const sanitizeResponseForStreaming = (response: AgentResponse, isStop: boolean): AgentResponse => {
  const normalizedSteps = normalizeReasoningSteps(response.steps)
  if (isStop) {
    return {
      ...response,
      steps: normalizedSteps,
    }
  }
  return {
    ...response,
    steps: normalizedSteps.map((step) => sanitizeStep(step))
  }
}

const sanitizeStep = (step: ReasoningStep): ReasoningStep => {
  if (isReasoningTextStep(step)) {
    return {
      ...step,
      reasoning: "",
    }
  }

  const eventMessages = step.eventMessages.slice(-STREAMING_EVENT_CAP)
  const output = typeof step.output === "string" ? step.output : sanitizeToolOutput(step.output)
  return {
    ...step,
    eventMessages,
    output
  }
}

const sanitizeToolOutput = (output: ToolOutput): ToolOutput => {
  if (typeof output === "string") return output

  switch (output.type) {
    case "web_search":
      return { type: "web_search", answer: "", searchResults: [] }
    case "memory_search":
      return { type: "memory_search", answer: "", references: [] }
    case "code_interpreter":
      return { type: "code_interpreter", status: "success", stdout: "", stderr: "", durationMs: 0 }
    case "display_weather_widget":
      return { type: "display_weather_widget", city: "" }
    case "display_stock_widget":
      return { type: "display_stock_widget", symbol: "" }
    case "display_image_search_widget":
      return { type: "display_image_search_widget", query: "", images: [] }
    case "image_generation":
      return { type: "image_generation", imageUrls: [] }
    case "change_note_kind":
      return { type: "change_note_kind", noteId: "", graphUid: "", kind: "" }
    case "reparent_note":
      return { type: "reparent_note", noteId: "", graphUid: "", parentId: null }
    case "delete_subtree":
      return { type: "delete_subtree", graphUid: "", deletedNodes: 0, deletedEdges: 0 }
    case "merge_notes":
      return { type: "merge_notes", targetId: "", graphUid: "", absorbed: 0 }
    case "split_note":
      return { type: "split_note", graphUid: "", createdIds: [], originalDeleted: false }
    case "relayout_board":
      return { type: "relayout_board", graphUid: "", moved: 0, mode: "default" }
    default:
      return output
  }
}

const collectNoteToolOutputs = (steps: ReasoningStep[]): Array<WriteNoteOutput | CreateNoteOutput | EditNoteOutput> =>
  steps.flatMap((step) => {
    if (!isToolCallStep(step)) return []
    if (
      (step.name === "write_note" || step.name === "create_note" || step.name === "edit_note") &&
      typeof step.output !== "string"
    ) {
      return [step.output as WriteNoteOutput | CreateNoteOutput | EditNoteOutput]
    }
    return []
  })


const collectLinkToolOutputs = (steps: ReasoningStep[]): LinkNotesOutput[] =>
  steps.flatMap((step) => {
    if (!isToolCallStep(step)) return []
    if (step.name === "link_notes" && typeof step.output !== "string") {
      return [step.output as LinkNotesOutput]
    }
    return []
  })


const STRUCT_TOOL_NAMES = [
  "change_note_kind",
  "reparent_note",
  "delete_subtree",
  "merge_notes",
  "split_note",
  "relayout_board",
] as const


/**
 * Collects the six structural tool outputs from the assistant reasoning
 * steps. Each output is dispatched to its corresponding AgentBridge
 * apply method in the post-stream block.
 */
const collectStructToolOutputs = (
  steps: ReasoningStep[],
): Array<ChangeNoteKindOutput | ReparentNoteOutput | DeleteSubtreeOutput | MergeNotesOutput | SplitNoteOutput | RelayoutOutput> =>
  steps.flatMap((step) => {
    if (!isToolCallStep(step)) return []
    if (
      (STRUCT_TOOL_NAMES as readonly string[]).includes(step.name)
      && typeof step.output !== "string"
    ) {
      return [step.output as ChangeNoteKindOutput | ReparentNoteOutput | DeleteSubtreeOutput | MergeNotesOutput | SplitNoteOutput | RelayoutOutput]
    }
    return []
  })

