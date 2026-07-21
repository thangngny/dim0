import { Conversation } from "./chat/conversation"
import { InputBar } from "./chat/input"
import { useListChats } from "../api/list-chats"
import { ChatProvider, useChat } from "../hooks/chat-context"
import { cn } from "@/lib/utils"
import { useMemo } from "react"
import type { Chat as ChatEntity } from "../types/chat"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger
} from "@/components/ui/dropdown-menu"
import { AddIcon, ChatHistoryIcon, ChatNewIcon, ClockIcon } from "@/components/icons"
import { useNavigate, useRouterState } from "@tanstack/react-router"
import { useAppStore } from "@/store"

type ChatProps = {
  chatId?: string
  initialBoardId?: string
  className?: string
  showHistoricalChats?: boolean
  preferChatRoute?: boolean
  enableSelectionContext?: boolean
  autoCreateBoard?: boolean
  /** Prefill composer once (research handoff). */
  initialDraft?: string | null
  onDraftConsumed?: () => void
}

const formatChatDate = (value?: string) => {
  if (!value) return null
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return null
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "numeric"
  }).format(date)
}

const HistoryList = ({
  chats,
  activeChatId,
  onSelectChat,
  onNewChat,
  variant
}: {
  chats: ChatEntity[]
  activeChatId?: string
  onSelectChat: (chatId: string) => void
  onNewChat: () => void
  variant: "inline" | "dropdown"
}) => {
  if (variant === "dropdown") {
    return (
      <div className="absolute top-0 w-full flex justify-start px-4 py-2 sm:px-8 z-50">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className="rounded-md border border-border shadow-sm bg-accent hover:bg-background transition-colors"
            >
              <ChatHistoryIcon className="size-4" strokeWidth={2} />
              <span className="sr-only">Open chat history</span>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-72 max-h-80 overflow-y-auto scrollbar-thin">
            <div className="px-2 py-1.5 text-xs font-medium text-muted-foreground uppercase tracking-wide flex flex-row items-center gap-0">
              <ClockIcon className="size-4 mr-2 inline-block" strokeWidth={2} />
              <span>Chat History</span>
            </div>
            <DropdownMenuItem
              onSelect={onNewChat}
              className="text-sm font-medium text-primary cursor-pointer flex flex-row items-center gap-0"
            >
              <AddIcon className="size-4 mr-2" strokeWidth={2} />
              <span>Create new chat</span>
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            {chats.length ? (
              chats.map(chat => {
                const subtitle = formatChatDate(chat.updatedAt || chat.createdAt)
                const isActive = chat.uid === activeChatId
                return (
                  <DropdownMenuItem
                    key={chat.uid}
                    onSelect={() => onSelectChat(chat.uid)}
                    className={cn(
                      "flex w-full max-w-full min-w-0 flex-col items-start gap-0.5",
                      isActive && "bg-accent/40"
                    )}
                  >
                    <span className="w-full truncate text-sm font-medium">{chat.label || "Untitled chat"}</span>
                    {
                      subtitle && (
                        <span className="w-full truncate text-xs text-muted-foreground">{subtitle}</span>
                      )
                    }
                  </DropdownMenuItem>
                )
              })
            ) : (
              <div className="px-4 py-8 text-center text-sm text-muted-foreground">
                No chats yet.
              </div>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    )
  }

  return (
    <div className="w-full max-w-[900px] mx-auto p-2 sm:p-4">
      <div className="flex items-center justify-between mb-2">
        <div className="text-sm font-medium text-muted-foreground flex flex-row items-center gap-0">
          <ClockIcon className="size-4 mr-2 inline-block" strokeWidth={2} />
          <span>Chat History</span>
        </div>
        <Button variant="ghost" size="sm" onClick={onNewChat} className="flex flex-row items-center gap-0">
          <ChatNewIcon className="size-4 mr-2" strokeWidth={2} />
          <span>New Chat</span>
        </Button>
      </div>
      {chats.length ? (
        <div className="max-h-64 overflow-y-auto scrollbar-thin flex flex-col gap-1 pr-1">
          {chats.map(chat => {
            const subtitle = formatChatDate(chat.updatedAt || chat.createdAt)
            const isActive = chat.uid === activeChatId
            return (
              <button
                key={chat.uid}
                onClick={() => onSelectChat(chat.uid)}
                className={cn(
                  "text-left rounded-md border px-3 py-2 transition-colors",
                  "hover:border-border hover:bg-accent/40",
                  isActive ? "border-border bg-accent/50" : "border-transparent bg-card/60"
                )}
              >
                <div className="text-sm font-medium truncate">{chat.label || "Untitled chat"}</div>
                {subtitle && (
                  <div className="text-xs text-muted-foreground">{subtitle}</div>
                )}
              </button>
            )
          })}
        </div>
      ) : (
        <div className="text-sm text-muted-foreground/80 rounded-md border border-dashed border-border px-4 py-6 text-center">
          No chats yet. Start a new one below.
        </div>
      )}
    </div>
  )
}

const ChatBody = ({
  initialBoardId,
  className,
  showHistoricalChats = false,
  preferChatRoute = false,
  enableSelectionContext = false,
  autoCreateBoard = false,
  initialDraft = null,
  onDraftConsumed,
}: ChatProps) => {
  const { chatId, setChatId } = useChat()
  const userId = useAppStore(s => s.userId)
  const { data: chatList = [] } = useListChats({ graphUid: initialBoardId, userId })
  const navigate = useNavigate()
  const routerLocation = useRouterState({ select: (s) => s.location })

  const attachedBoardId = useMemo(() => {
    const currentChat = chatList?.find(c => c.uid === chatId)
    return currentChat?.graphUid || initialBoardId
  }, [chatList, chatId, initialBoardId])

  const historicalChats = showHistoricalChats && initialBoardId
    ? chatList
    : []

  const historyVariant: "inline" | "dropdown" =
    showHistoricalChats && chatId ? "dropdown" : "inline"

  const chatClassName = cn(
    "absolute inset-0 h-full w-full overflow-hidden flex flex-col",
    showHistoricalChats ? "gap-4" : "items-center",
    className
  )

  const isBoardRoute = routerLocation.pathname?.startsWith("/boards/")

  const syncBoardUrl = (nextChatId?: string) => {
    if (!isBoardRoute) return
    // Stay on whatever board-family path we're on (board, sheet, code,
    // widget) — `to: "."` keeps the current pathname so an active surface
    // panel isn't closed when the chat changes. We only swap the search.
    navigate({
      to: ".",
      search: (prev: Record<string, unknown>) => ({
        ...prev,
        current_chat_id: nextChatId || undefined,
      }),
    })
  }

  const handleNewChat = () => {
    setChatId(undefined)

    if (isBoardRoute) {
      syncBoardUrl(undefined)
      return
    }

    if (routerLocation.pathname?.startsWith("/chats/")) {
      navigate({ to: "/chats" })
    }
  }

  return (
    <div className={chatClassName}>
      {showHistoricalChats && (
        <HistoryList
          chats={historicalChats}
          activeChatId={chatId}
          onSelectChat={(id) => {
            setChatId(id)
            syncBoardUrl(id)
          }}
          onNewChat={handleNewChat}
          variant={historyVariant}
        />
      )}

      <div className="absolute top-4 right-4 z-50 md:hidden">
        <Button
          variant="ghost"
          size="icon"
          className="rounded-md border border-border shadow-sm bg-accent hover:bg-background transition-colors"
          onClick={handleNewChat}
          aria-label="Create new chat"
          title="Create new chat"
        >
          <ChatNewIcon className="size-4" strokeWidth={2} />
        </Button>
      </div>

      <div className={cn(
        "w-full min-h-0",
        chatId ? "flex-1" : "flex-none",
        showHistoricalChats ? "flex flex-col items-center" : "flex flex-col"
      )}>
        {chatId ? (
          <div className="w-full min-w-0 h-full p-4 overflow-auto scrollbar-thin">
            <div className="w-full h-full flex flex-col items-center justify-center">
              <div className="w-full max-w-[800px] h-full">
                <Conversation chatId={chatId} />
              </div>
            </div>
          </div>
        ) : null}
      </div>

      <InputBar
        attachedBoardId={attachedBoardId}
        layout={showHistoricalChats ? "docked" : "floating"}
        preferChatRoute={preferChatRoute}
        enableSelectionContext={enableSelectionContext}
        autoCreateBoard={autoCreateBoard}
        initialDraft={initialDraft}
        onDraftConsumed={onDraftConsumed}
      />
    </div>
  )
}

/**
 * Chat view component
 */
export const Chat = (props: ChatProps) => {
  const { chatId } = props

  return (
    <ChatProvider initialChatId={chatId}>
      <ChatBody {...props} />
    </ChatProvider>
  )
}
