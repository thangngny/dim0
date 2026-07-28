/**
 * Reconnect state machine + backoff math for the WS collab adapter.
 *
 * Lives outside `use-ws-collab.ts` so the timing logic is testable in
 * isolation with fake timers — the WS adapter itself is hard to unit-
 * test (WebSocket mocking, room handshake, etc.) but the reconnect
 * timing is the part most likely to regress and the most useful to
 * pin down.
 *
 * Subscribers (the chrome status indicator) read the current state
 * via `getCollabConnState` / `useCollabConnState`. The adapter calls
 * `setCollabConnState` to drive transitions. Module-singleton scope
 * because there's exactly one active board at a time.
 */
import { useSyncExternalStore } from "react"


export type CollabConnState =
  | "idle"          // collab disabled / no board
  | "connecting"    // socket opening, no welcome yet
  | "live"          // welcome received, normal operation
  | "reconnecting"  // unexpected close, backoff pending
  | "failed"        // max reconnect attempts reached
  | "room-full"     // server rejected with 4429 (board at plan-tier user cap)


/**
 * Exponential backoff with a hard cap. Returns the delay to wait
 * *before* the n-th retry attempt (0-indexed: attempt=0 → 1s,
 * attempt=1 → 2s, attempt=2 → 4s, …).
 *
 * Caps at 30s so a long-disconnected tab doesn't sit on a 17-minute
 * timer when the network finally returns.
 */
export const computeBackoffMs = (
  attempt: number,
  opts: { baseDelayMs?: number; capDelayMs?: number } = {},
): number => {
  const base = opts.baseDelayMs ?? 1_000
  const cap = opts.capDelayMs ?? 30_000
  const raw = base * Math.pow(2, Math.max(0, attempt))
  return Math.min(raw, cap)
}


export const MAX_RECONNECT_ATTEMPTS = 6


let currentState: CollabConnState = "idle"
const listeners = new Set<() => void>()

/**
 * Imperative reconnect handle registered by `useWsCollab` while a board
 * is mounted. The status pill's "Reconnect" button calls
 * `requestCollabReconnect` so a user can escape the terminal `failed` /
 * `room-full` state without a full page refresh. `null` when no board
 * is active (sign-out, board list) so the call is a safe no-op.
 */
let reconnectTrigger: (() => void) | null = null


/** Registered by the WS hook on mount; cleared on unmount. */
export const setCollabReconnectTrigger = (fn: (() => void) | null): void => {
  reconnectTrigger = fn
}


/**
 * Request a reconnect from outside the hook (status pill button,
 * `online` event in non-hook code). No-op when no board is mounted.
 */
export const requestCollabReconnect = (): void => {
  reconnectTrigger?.()
}


/** Non-React reader — used in non-component code paths. */
export const getCollabConnState = (): CollabConnState => currentState


/**
 * Driver setter used by `use-ws-collab.ts`. Notifies subscribers
 * synchronously; React batches the re-render at its own cadence.
 */
export const setCollabConnState = (next: CollabConnState): void => {
  if (next === currentState) return
  currentState = next
  for (const cb of listeners) cb()
}


/** Reactive subscription for the status indicator component. */
export const useCollabConnState = (): CollabConnState => {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb)
      return () => listeners.delete(cb)
    },
    () => currentState,
    () => "idle",
  )
}


/**
 * Reset to `idle` — used by tests and by the adapter's cleanup path
 * when the WS hook unmounts (board navigation, sign-out).
 */
export const resetCollabConnState = (): void => {
  currentState = "idle"
  for (const cb of listeners) cb()
}
