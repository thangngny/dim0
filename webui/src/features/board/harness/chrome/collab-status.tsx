import { cn } from "@/lib/utils"
import { requestCollabReconnect, useCollabConnState } from "../canvas/collab-reconnect"


type StatusKey = "idle" | "connecting" | "live" | "reconnecting" | "failed" | "room-full"


const STATUS_LABEL: Record<StatusKey, string> = {
  idle: "",
  connecting: "Connecting…",
  live: "",
  reconnecting: "Reconnecting…",
  failed: "Disconnected",
  "room-full": "Board is full",
}


const STATUS_CLASS: Record<StatusKey, string> = {
  idle: "",
  connecting: "text-muted-foreground",
  live: "",
  reconnecting: "text-amber-600 dark:text-amber-400",
  failed: "text-destructive",
  "room-full": "text-amber-600 dark:text-amber-400",
}


/**
 * Collab WS status pill. Hidden during normal operation (`idle`, `live`)
 * so it doesn't squat in the chrome; surfaces during transitions
 * (`connecting` on first open, `reconnecting` during backoff) and
 * after max-attempts (`failed`).
 *
 * In the terminal `failed` / `room-full` states a "Reconnect" button is
 * offered so the user can retry without a full page refresh — the WS
 * hook also auto-retries on `online` / refocus / a 20s poll, but the
 * button is the explicit escape hatch.
 *
 * Layout owned by the caller — sits next to the save-status pill in
 * `harness-canvas.tsx`.
 */
export function HarnessCollabStatus() {
  const state = useCollabConnState()
  const label = STATUS_LABEL[state]
  if (!label) return null
  const showReconnect = state === "failed" || state === "room-full"
  return (
    <div
      className={cn(
        "flex items-center gap-1.5 rounded-md border border-border bg-background/95 px-2 py-1 text-xs shadow-sm backdrop-blur",
        STATUS_CLASS[state],
      )}
      aria-live="polite"
      role="status"
    >
      <span>{label}</span>
      {showReconnect && (
        <button
          type="button"
          onClick={() => requestCollabReconnect()}
          className="rounded border border-current/40 px-1.5 py-0.5 font-medium hover:bg-current/10"
        >
          Reconnect
        </button>
      )}
    </div>
  )
}
