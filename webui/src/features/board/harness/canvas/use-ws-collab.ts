import { useEffect } from "react"
import {
  attachSync,
  midpointToCubicControls,
  type CanvasStore,
  type ClientId,
  type Edge,
  type Node,
  type Op,
  type OpBatch,
  type PresencePatch,
  type PresenceState,
  type SyncAdapter,
  type Vec2,
} from "@canvas-harness/core"
import camelcaseKeys from "camelcase-keys"
import { mintCollabTicket } from "@/features/board/api/collab-ticket"
import { API_URL } from "@/config/api"
import { notifyWsClose } from "@/features/connection/connection-state"
import type { Graph } from "@/features/board/types/board"
import { useAppStore } from "@/store"
import { applyGraphToStore } from "../persist/snapshot-load"
import {
  MAX_RECONNECT_ATTEMPTS,
  computeBackoffMs,
  getCollabConnState,
  resetCollabConnState,
  setCollabConnState,
  setCollabReconnectTrigger,
} from "./collab-reconnect"
import { createPresenceThrottle } from "./presence-throttle"
import {
  adaptEdgeColors,
  adaptNodeColors,
  applyColorsToEdgeStyle,
  applyColorsToStyle,
  type StoredColors,
  type StoredEdgeColors,
} from "../theme/color-adapter"
import { getBoardThemeMode } from "../theme/theme-mode-ref"
import { normalizeBatchAutoFit } from "./normalize-autofit"
import { restoreCollapsedGroups } from "./collapsed-groups"


/**
 * Phase 1a WebSocket adapter for cross-machine collab.
 *
 * Wire shape mirrors `@canvas-harness/sync-broadcast` — the backend is
 * a pure relay for v1a (no sequencing, no persistence), so we use the
 * same on-wire kinds:
 *
 *   send/receive:
 *     { kind: "batch",          batch }
 *     { kind: "presence",       clientId, state }
 *     { kind: "presence-leave", clientId }
 *     { kind: "hello",          clientId }
 *
 * Reconnect + `since_seq` catch-up land in Phase 1c; for now a dropped
 * socket means the user needs to refresh.
 */
export const useWsCollab = (
  store: CanvasStore,
  boardId: string | null,
  enabled: boolean,
  rootId?: string | null,
): void => {
  const userEmail = useAppStore((s) => s.userEmail)
  const userId = useAppStore((s) => s.userId)
  const userName = userEmail.split("@")[0] || "Anonymous"

  useEffect(() => {
    if (!enabled || !boardId) return

    // Outer reconnect loop. The adapter is recreated on each attempt;
    // `lastSeq` carries the highest seen seq across attempts so the
    // reconnect's `hello.since_seq` is accurate. `attempt` resets on
    // every successful welcome — the chain has to clear the welcome
    // handshake, not just open the socket, to count as a recovery.
    let cancelled = false
    let teardown: (() => void) | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let attempt = 0
    let lastSeq = 0
    setCollabConnState("connecting")

    const cancelReconnect = () => {
      if (reconnectTimer !== null) {
        clearTimeout(reconnectTimer)
        reconnectTimer = null
      }
    }

    const scheduleReconnect = () => {
      if (cancelled) return
      if (attempt >= MAX_RECONNECT_ATTEMPTS) {
        setCollabConnState("failed")
        return
      }
      const delay = computeBackoffMs(attempt)
      attempt += 1
      setCollabConnState("reconnecting")
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null
        void setup()
      }, delay)
    }

    const setup = async () => {
      if (cancelled) return
      setCollabConnState("connecting")
      let ticket: string
      try {
        const res = await mintCollabTicket(boardId)
        ticket = res.ticket
      } catch (err) {
        console.warn("collab: failed to mint ticket", err)
        scheduleReconnect()
        return
      }
      if (cancelled) return

      const color = colorForId(userId ?? store.clientId)
      store.presence.setLocal({
        name: userName || "Anonymous",
        color,
        cursor: null,
        selection: [],
        editing: null,
      })

      // `since_seq` rides the URL so the server can dispatch the welcome
      // mode at connect time — `hello` wouldn't arrive until after the
      // welcome had already been sent. Omitted on first connect.
      //
      // `root_id` also rides the URL when the user is viewing a folder
      // so the welcome snapshot is scoped to the same view REST loaded.
      // Without it the WS welcome would carry the whole board and
      // overwrite the folder-scoped store.
      const sinceSeqParam = lastSeq > 0 ? `&since_seq=${lastSeq}` : ""
      const rootIdParam = rootId ? `&root_id=${encodeURIComponent(rootId)}` : ""
      const url = `${wsBaseFromApiUrl(API_URL)}/boards/${boardId}/collab?ticket=${encodeURIComponent(ticket)}${sinceSeqParam}${rootIdParam}`
      const adapter = createWebSocketSyncAdapter({
        url,
        clientId: store.clientId,
        initialPresence: store.presence.getLocal(),
        store,
        sinceSeq: lastSeq > 0 ? lastSeq : null,
        onWelcome: (seq) => {
          lastSeq = seq
          attempt = 0
          setCollabConnState("live")
          // The welcome snapshot (mode "merge") re-applies server node
          // state, which doesn't carry `hidden` — re-apply persisted
          // collapsed clusters so a reload keeps collapsed groups hidden.
          restoreCollapsedGroups(store, boardId)
        },
        onClose: (code, seqAtClose) => {
          lastSeq = seqAtClose
          // Detach the dead adapter before scheduling the next attempt
          // so attachSync isn't trying to push to a closed socket.
          teardown?.()
          teardown = null
          if (cancelled) return
          if (code === 1000 || code === 1001) {
            // Clean close — leave state as live if user navigated;
            // the cleanup path resets to idle on unmount.
            return
          }
          // 4429 = WS_ROOM_FULL (server collab.py). The board is at the
          // plan-tier concurrent-user cap; retrying won't help until a
          // peer leaves. Surface a terminal state with a clear banner
          // rather than spinning the reconnect loop forever.
          if (code === WS_ROOM_FULL) {
            setCollabConnState("room-full")
            return
          }
          scheduleReconnect()
        },
      })
      const detachSync = attachSync(store, adapter)

      teardown = () => {
        detachSync()
        store.presence.setLocal({ cursor: null, selection: [], editing: null })
      }
    }

    // Cancel any pending backoff and start a fresh connect attempt
    // immediately. Used by the `online` event, the visibility-change
    // fast-retry, and the imperative `requestCollabReconnect` handle
    // (status pill button) so all three share one reset path.
    const retryNow = () => {
      if (cancelled) return
      cancelReconnect()
      attempt = 0
      void setup()
    }
    // Expose the retry handle so the collab-status pill can offer a
    // "Reconnect" button that escapes the terminal `failed` / `room-full`
    // state without a full page refresh.
    setCollabReconnectTrigger(retryNow)

    // Visibility-change: when the tab is foregrounded after being
    // backgrounded long enough for the WS to die, retry immediately
    // instead of waiting out the current backoff. Mobile Safari does
    // this aggressively (~5 min); the user's perception is "I opened
    // the tab back up and it just worked." Also fires from the terminal
    // `failed` state so a user who backgrounded the tab during an
    // outage recovers on refocus instead of seeing "Disconnected".
    const onVisibilityChange = () => {
      if (document.visibilityState !== "visible") return
      const st = getCollabConnState()
      if (st !== "reconnecting" && st !== "failed") return
      retryNow()
    }
    document.addEventListener("visibilitychange", onVisibilityChange)

    // `online` event: the browser regained network after an offline
    // spell. The WS may have already burned through MAX_RECONNECT_ATTEMPTS
    // while the network was down (landing in terminal `failed`); without
    // this, the user would have to refresh even though connectivity is
    // back. Retry from both `reconnecting` and `failed`.
    const onOnline = () => {
      const st = getCollabConnState()
      if (st === "reconnecting" || st === "failed") retryNow()
    }
    window.addEventListener("online", onOnline)

    // Slow background retry from the terminal `failed` state. The
    // `online` + visibility handlers cover the common cases, but some
    // environments (background tab throttling, flaky NICs that don't
    // fire `online`) can leave the user stranded in `failed` with no
    // event to wake the loop. A 20s poll is cheap and self-stops once
    // a retry succeeds (state leaves `failed`).
    const failedRetryTimer = setInterval(() => {
      if (cancelled) return
      if (getCollabConnState() === "failed") retryNow()
    }, 20_000)

    void setup()

    return () => {
      cancelled = true
      cancelReconnect()
      setCollabReconnectTrigger(null)
      document.removeEventListener("visibilitychange", onVisibilityChange)
      window.removeEventListener("online", onOnline)
      clearInterval(failedRetryTimer)
      teardown?.()
      teardown = null
      resetCollabConnState()
    }
  }, [store, boardId, enabled, userName, userId, userEmail, rootId])
}


/**
 * Build a `ws(s)://host` base from the configured HTTP API URL.
 * `http://` → `ws://`, `https://` → `wss://`.
 */
const wsBaseFromApiUrl = (apiUrl: string): string => apiUrl.replace(/^http/i, "ws")


/**
 * WebSocket close codes the server can emit. Mirrors the constants in
 * `backend/topix/api/router/collab.py` (must stay in sync).
 *
 * `WS_ROOM_FULL` (4429) fires when the board is at its plan-tier
 * concurrent-user cap; reconnect won't help until a peer leaves, so
 * the outer loop terminates instead of retrying.
 */
const WS_ROOM_FULL = 4429


/**
 * Cross-theme color fix: in-place rewrite `style.{bg,stroke,text}` on
 * every node/edge op so they match the LOCAL theme regardless of the
 * sender's theme. The canonical truth always lives in
 * `data._storedColors` (canonical Tailwind hex); the receiver projects
 * it through `adaptNodeColors / adaptEdgeColors` for paint.
 *
 * Mutates in place — the batch object is owned by us at this point
 * (decoded from JSON moments ago) and `attachSync` reads style after
 * we return.
 */
const normalizeBatchColorsForLocalTheme = (batch: OpBatch): void => {
  const mode = getBoardThemeMode()
  for (const op of batch.ops) {
    rewriteOpColors(op, mode)
  }
}


const rewriteOpColors = (op: Op, mode: "light" | "dark"): void => {
  // `op` is one of canvas-harness's tagged unions; we mutate `node` or
  // `patch` based on the variant. Reads/writes go through Partial<Node>
  // / Partial<Edge> casts to avoid type-narrowing acrobatics.
  if (op.type === "node.add") {
    rewriteNodeStyle(op.node, mode)
    return
  }
  if (op.type === "node.update") {
    rewriteNodeStyle(op.patch as Partial<Node>, mode)
    return
  }
  if (op.type === "edge.add") {
    rewriteEdgeStyle(op.edge, mode)
    return
  }
  if (op.type === "edge.update") {
    rewriteEdgeStyle(op.patch as Partial<Edge>, mode)
    return
  }
}


const rewriteNodeStyle = (target: Partial<Node>, mode: "light" | "dark"): void => {
  const data = target.data as { _storedColors?: StoredColors } | undefined
  const stored = data?._storedColors
  if (!stored) return
  const display = adaptNodeColors(stored, mode)
  target.style = applyColorsToStyle(target.style ?? {}, display)
}


const rewriteEdgeStyle = (target: Partial<Edge>, mode: "light" | "dark"): void => {
  const data = target.data as { _storedColors?: StoredEdgeColors } | undefined
  const stored = data?._storedColors
  if (!stored) return
  const display = adaptEdgeColors(stored, mode)
  target.style = applyColorsToEdgeStyle(target.style ?? {}, display)
}


/**
 * Patch any attached EdgeEnd that arrived without a `localOffset`.
 *
 * canvas-harness crashes in `projectEndToWorld → nodeLocalToWorld` if
 * an attached endpoint has only `{nodeId}` — the missing `localOffset`
 * is read as `undefined.x`. Server-side conversion now defaults the
 * offset to node center, but this in-receiver pass is the safety net:
 * legacy payloads, future bypasses, or a server bug can't take the
 * tab down.
 *
 * If the receiver's local store has the referenced node, default to
 * center `(w/2, h/2)`. If not (race with a not-yet-applied node.add),
 * default to `(0, 0)` so the projection succeeds — the edge endpoint
 * will visually sit at the node's top-left until the next refresh.
 */
const patchMissingEdgeEndpoints = (batch: OpBatch, store: CanvasStore): void => {
  for (const op of batch.ops) {
    if (op.type === "edge.add") {
      const edge = op.edge as Edge & { _midpoint?: Vec2 }
      patchEdgeEnd(edge.source, store)
      patchEdgeEnd(edge.target, store)
      // pathStyle is non-optional on Edge — canvas-harness's `samplesFor`
      // returns undefined when it's missing, then `edgeAABBFromSamples`
      // crashes on `.length`. Default to bezier (matches Dim0's
      // LinkStyle.path_style default).
      if (!edge.pathStyle) edge.pathStyle = "bezier"
      // Server ships the curve midpoint as `_midpoint` (world coords)
      // and lets the receiver compute cubic controls with its own
      // endpoint world coords. Symmetric to `enrichBatchWithMidpoint`
      // outbound. Skip when controls are already provided by the
      // sender.
      if (edge._midpoint && !edge.control) {
        expandMidpointToControl(edge, store)
      }
      delete edge._midpoint
    } else if (op.type === "edge.update") {
      const patch = op.patch as Partial<Edge> & { _midpoint?: Vec2 }
      if (patch.source) patchEdgeEnd(patch.source, store)
      if (patch.target) patchEdgeEnd(patch.target, store)
      if (patch._midpoint && !patch.control) {
        expandUpdateMidpointToControl(op.id, patch, store)
      }
      delete patch._midpoint
    }
  }
}


const expandMidpointToControl = (
  edge: Edge & { _midpoint?: Vec2 },
  store: CanvasStore,
): void => {
  const midpoint = edge._midpoint
  if (!midpoint) return
  const sourceWorld = endpointWorld(edge.source, store)
  const targetWorld = endpointWorld(edge.target, store)
  if (!sourceWorld || !targetWorld) return
  const { c1, c2 } = midpointToCubicControls(sourceWorld, midpoint, targetWorld)
  edge.control = [c1, c2]
}


const expandUpdateMidpointToControl = (
  edgeId: Edge["id"],
  patch: Partial<Edge> & { _midpoint?: Vec2 },
  store: CanvasStore,
): void => {
  const midpoint = patch._midpoint
  if (!midpoint) return
  const existing = store.getEdge(edgeId)
  const sourceEnd = patch.source ?? existing?.source
  const targetEnd = patch.target ?? existing?.target
  if (!sourceEnd || !targetEnd) return
  const sourceWorld = endpointWorld(sourceEnd, store)
  const targetWorld = endpointWorld(targetEnd, store)
  if (!sourceWorld || !targetWorld) return
  const { c1, c2 } = midpointToCubicControls(sourceWorld, midpoint, targetWorld)
  patch.control = [c1, c2]
}


const patchEdgeEnd = (end: unknown, store: CanvasStore): void => {
  if (!end || typeof end !== "object") return
  const e = end as { nodeId?: string; localOffset?: { x: number; y: number } }
  if (!("nodeId" in e) || !e.nodeId) return
  if (e.localOffset && typeof e.localOffset.x === "number") return
  const node = store.getNode(e.nodeId as Edge["source"] extends { nodeId: infer N } ? N : never)
  if (node) {
    e.localOffset = { x: node.w / 2, y: node.h / 2 }
  } else {
    e.localOffset = { x: 0, y: 0 }
  }
}


/**
 * Collapse repeat update ops on the same target into one.
 *
 * canvas-harness emits a `store.updateEdge` / `store.updateNode` PER
 * pointermove for the edge-midpoint and node-resize gestures (see the
 * lib's use-interaction-gesture.ts). The receiver doesn't need each
 * intermediate value — the final patch is all that matters for
 * visual sync. We keep the EARLIEST `prev` (the gesture's starting
 * point so conflict detection still has a meaningful anchor) and
 * shallow-merge the patches in arrival order (last value wins per
 * field). Non-update ops pass through untouched and in order.
 */
const dedupeRepeatUpdates = (ops: Op[]): Op[] => {
  const out: Op[] = []
  const indexByKey = new Map<string, number>()
  for (const op of ops) {
    if (op.type === "node.update" || op.type === "edge.update") {
      const key = `${op.type}:${op.id as unknown as string}`
      const prevIdx = indexByKey.get(key)
      if (prevIdx !== undefined) {
        // Reuse the earlier slot — merge new patch on top, keep
        // earliest `prev`. The op union forces us through `any` here.
        const earlier = out[prevIdx] as { patch: Record<string, unknown> }
        const incoming = op as { patch: Record<string, unknown> }
        earlier.patch = { ...earlier.patch, ...incoming.patch }
        continue
      }
      indexByKey.set(key, out.length)
      out.push(op)
      continue
    }
    out.push(op)
  }
  return out
}


/**
 * Compute the on-curve midpoint world point from cubic-bezier control
 * points and attach it as `_midpoint` so the server can persist it
 * directly into `LinkProperties.edge_control_point`.
 *
 * Inverse of canvas-harness's `midpointToCubicControls`: with symmetric
 * c1 = c2 = c the curve at t=0.5 passes through `(S + T + 6·c) / 8`.
 * We do the math client-side so the server stays stateless (no need
 * to look up node positions just to recompute geometry).
 */
const enrichBatchWithMidpoint = (batch: OpBatch, store: CanvasStore): void => {
  for (const op of batch.ops) {
    if (op.type === "edge.add") {
      attachEdgeMidpoint(op.edge as Edge & { _midpoint?: { x: number; y: number } }, op.edge, store)
    } else if (op.type === "edge.update") {
      attachEdgeMidpoint(
        op.patch as Partial<Edge> & { _midpoint?: { x: number; y: number } },
        store.getEdge(op.id) ?? null,
        store,
        op.patch,
      )
    }
  }
}


const attachEdgeMidpoint = (
  target: Partial<Edge> & { _midpoint?: { x: number; y: number } },
  baseEdge: Edge | null,
  store: CanvasStore,
  patchOverride?: Partial<Edge>,
): void => {
  const control = (patchOverride?.control ?? target.control ?? baseEdge?.control) as
    | ReadonlyArray<{ x: number; y: number }>
    | undefined
  if (!control || control.length === 0) return
  // Source/target on the patch override take precedence (an endpoint
  // change in the same batch); else read from the existing edge.
  const sourceEnd = patchOverride?.source ?? baseEdge?.source
  const targetEnd = patchOverride?.target ?? baseEdge?.target
  if (!sourceEnd || !targetEnd) return
  const sourceWorld = endpointWorld(sourceEnd, store)
  const targetWorld = endpointWorld(targetEnd, store)
  if (!sourceWorld || !targetWorld) return
  const c = control[0]
  target._midpoint = {
    x: (sourceWorld.x + targetWorld.x + 6 * c.x) / 8,
    y: (sourceWorld.y + targetWorld.y + 6 * c.y) / 8,
  }
}


const endpointWorld = (
  end: Edge["source"] | Edge["target"],
  store: CanvasStore,
): { x: number; y: number } | null => {
  if ("nodeId" in end) {
    const node = store.getNode(end.nodeId)
    if (!node) return null
    return { x: node.x + end.localOffset.x, y: node.y + end.localOffset.y }
  }
  return end.worldPoint
}


/**
 * Deterministic-ish HSL color per client. Same user across tabs gets
 * the same color; different users get different ones.
 */
const colorForId = (id: string): string => {
  let h = 0
  for (let i = 0; i < id.length; i += 1) {
    h = (h * 31 + id.charCodeAt(i)) | 0
  }
  const hue = Math.abs(h) % 360
  return `hsl(${hue} 70% 55%)`
}


type OutboundMessage =
  | { kind: "op"; client_seq: number; batch: OpBatch }
  | { kind: "presence"; clientId: ClientId; state: PresenceState }
  | { kind: "presence-leave"; clientId: ClientId }
  | { kind: "hello"; clientId: ClientId; since_seq?: number | null }

type InboundMessage =
  | {
      kind: "welcome"
      mode?: "snapshot" | "catch-up" | "live"
      seq: number
      snapshot?: Graph
      batches?: OpBatch[]
      presence?: Record<string, PresenceState>
    }
  | { kind: "peer-op"; seq: number; batch: OpBatch }
  | { kind: "op-applied"; seq: number; client_seq: number }
  | { kind: "op-rejected"; client_seq: number; reason?: string }
  | { kind: "presence"; clientId: ClientId; state: PresenceState }
  | { kind: "presence-leave"; clientId: ClientId }
  | { kind: "hello"; clientId: ClientId }
  | { kind: "kick"; reason?: string }
  | {
      kind: "research-progress"
      board_id?: string
      session_id?: string
      ts?: number
      event?: {
        id?: string
        event_type: string
        label?: string
        agent_id?: string
        role?: string
        detail?: string
        query?: string
        ts?: number
      }
      snapshot?: {
        board_id: string
        session_id: string
        mode?: string
        last_event?: string
        last_label?: string
        completed?: boolean
        failed?: boolean
        active?: boolean
        nodes_seen?: number
        agents?: Array<{
          agent_id: string
          role: string
          label: string
          status: string
          detail: string
          query: string
          updated_at?: number
        }>
        events?: Array<{
          id?: string
          event_type: string
          label?: string
          agent_id?: string
          role?: string
          detail?: string
          query?: string
          ts?: number
        }>
        updated_at?: number
      }
    }


type WebSocketSyncOptions = {
  url: string
  clientId: ClientId
  initialPresence?: PresenceState
  /**
   * Required for outbound enrichment — we look up node positions to
   * compute the edge midpoint from the cubic-bezier control points
   * (the server stores midpoint as a position; the math needs the
   * endpoint world coords).
   */
  store: CanvasStore
  /**
   * Highest server seq this client has previously seen — sent on
   * `hello { since_seq }` so a reconnecting peer can catch up on
   * missed peer-ops without a full snapshot rebuild. Pass `null` on
   * first connect.
   */
  sinceSeq?: number | null
  /** Fired after `welcome` is applied; receives the welcome's seq. */
  onWelcome?: (seq: number) => void
  /**
   * Fired on `close`, with the close code and the highest seq the
   * adapter saw before going down. The outer reconnect loop uses this
   * to decide whether to schedule a retry and what `sinceSeq` to send
   * on the next attempt.
   */
  onClose?: (code: number, lastSeq: number) => void
}


/**
 * Build a `SyncAdapter` over a single WebSocket. Mirrors the
 * BroadcastChannel adapter's contract — outbound frames are JSON of
 * `WireMessage`; inbound frames are dispatched to batch / presence
 * listeners. Self-echo is impossible because the server already
 * excludes the sender.
 */
const createWebSocketSyncAdapter = ({
  url,
  clientId,
  initialPresence,
  store,
  sinceSeq,
  onWelcome,
  onClose,
}: WebSocketSyncOptions): SyncAdapter => {
  const ws = new WebSocket(url)
  const batchListeners = new Set<(batch: OpBatch) => void>()
  const presenceListeners = new Set<(clientId: ClientId, state: PresenceState | null) => void>()
  let lastLocalPresence = initialPresence
  let openSent = false
  const outbox: OutboundMessage[] = []

  // Local monotonic counter so the client can match `op-applied` acks
  // back to outgoing ops. Server stamps the global `seq` separately.
  let clientSeq = 0
  // Highest server `seq` we've observed. Seeded from the caller's
  // `sinceSeq` so reconnect attempts pick up where the previous
  // adapter left off; the outer loop also reads this back on close
  // so the next attempt can send the right `since_seq`.
  let lastServerSeq = sinceSeq ?? 0

  // Outbound coalesce window — buffer local op batches for a brief
  // window and merge their ops into a single wire message. Caps
  // typing-burst load on the server (1 OpenAI embed call per merged
  // batch instead of one per keystroke) while staying well below
  // perceptual "live" latency for peers.
  const COALESCE_MS = 75
  const pendingOps: { client_seq: number; batch: OpBatch }[] = []
  let coalesceTimer: ReturnType<typeof setTimeout> | null = null

  const send = (msg: OutboundMessage) => {
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(msg))
    } else {
      outbox.push(msg)
    }
  }

  const flushPendingOps = () => {
    coalesceTimer = null
    if (pendingOps.length === 0) return
    const first = pendingOps[0]
    const last = pendingOps[pendingOps.length - 1]
    // Concatenate ops in arrival order; server applies them sequentially
    // so causal intent is preserved. Use the latest batch's id/ts/origin
    // so the merged batch identifies as the most recent action.
    const concatenated = pendingOps.flatMap((p) => p.batch.ops)
    const mergedBatch: OpBatch = {
      ...first.batch,
      id: last.batch.id,
      ts: last.batch.ts,
      origin: last.batch.origin,
      // Collapse consecutive update ops on the same target so a 5-second
      // edge-midpoint drag or node-resize doesn't ship 60+ wire ops for
      // the same id. Each gesture's last patch carries the cumulative
      // final state; the earliest `prev` is preserved for conflict
      // detection.
      ops: dedupeRepeatUpdates(concatenated),
    }
    // Take the most-recent client_seq — the server's op-applied for
    // that one implicitly acks the older ones (a single seq covers the
    // merged write).
    send({ kind: "op", client_seq: last.client_seq, batch: mergedBatch })
    pendingOps.length = 0
  }

  // Presence throttle (3.2). Cursors fire on every pointermove which is
  // ~60Hz; broadcasting them all-to-all is N² over peer count. Leading
  // + trailing edge throttle to ~20Hz: peers see the cursor pop in
  // immediately (leading), and a stopping cursor still lands its final
  // position within one window (trailing). Non-cursor structural
  // changes (selection, editing) ride the same throttle for simplicity
  // — they're surfaced within ≤ 50ms which is below the perceptual
  // threshold. Helper lives in `presence-throttle.ts` so the timer
  // logic is unit-testable with fake timers.
  const presenceThrottle = createPresenceThrottle<PresenceState>(
    (state) => send({ kind: "presence", clientId, state }),
    { windowMs: 50 },
  )

  const queueOp = (client_seq: number, batch: OpBatch) => {
    // Compute the edge midpoint once per batch from the local store
    // (source/target endpoints + control[c1, c2]); the server stores
    // the midpoint as a position. See `enrichBatchWithMidpoint`.
    enrichBatchWithMidpoint(batch, store)
    pendingOps.push({ client_seq, batch })
    if (coalesceTimer === null) {
      coalesceTimer = setTimeout(flushPendingOps, COALESCE_MS)
    }
  }

  ws.addEventListener("open", () => {
    if (!openSent) {
      openSent = true
      // `since_seq` is the highest server-stamped seq we've ever seen
      // on this client. On first connect it's null → server replies
      // with `mode: "snapshot"`. On reconnect it's the last-known seq
      // so the server can decide between catch-up / live / fresh
      // snapshot (Phase 1c.2 wires this on the server side; the
      // wire shape is already defined here).
      const sinceSeqOut = lastServerSeq > 0 ? lastServerSeq : null
      send({ kind: "hello", clientId, since_seq: sinceSeqOut })
      if (lastLocalPresence) {
        send({ kind: "presence", clientId, state: lastLocalPresence })
      }
    }
    while (outbox.length > 0) {
      const msg = outbox.shift()
      if (msg) ws.send(JSON.stringify(msg))
    }
  })

  ws.addEventListener("message", (e: MessageEvent) => {
    let msg: InboundMessage
    try {
      msg = JSON.parse(typeof e.data === "string" ? e.data : "") as InboundMessage
    } catch {
      return
    }
    if (msg.kind === "welcome") {
      lastServerSeq = msg.seq
      // Phase 1c.1: snapshot mode reconciles the canvas-harness store
      // against the welcome's graph payload. Uses `mode: "merge"` —
      // diff against current store state and apply minimal changes,
      // never wipe — so a server-side DB hiccup that yields `{}`
      // doesn't blank the canvas, and so the REST-loaded state is
      // preserved when the welcome is equivalent. Undo history stays
      // intact across reconnects.
      //
      // The server ships Pydantic's raw `model_dump()` (snake_case);
      // `applyGraphToStore` uses the same converters as the REST path,
      // which expect camelCase. Mirrors the `camelcaseKeys` call in
      // `getBoard`.
      if (msg.mode === "snapshot" && msg.snapshot) {
        try {
          const graph = camelcaseKeys(
            msg.snapshot as unknown as Record<string, unknown>,
            { deep: true },
          ) as unknown as Graph
          applyGraphToStore(store, graph, { mode: "merge" })
        } catch (err) {
          console.warn("collab: failed to apply welcome snapshot", err)
        }
      }
      // Phase 1c.2: catch-up mode delivers the slice of batches the
      // peer missed while disconnected. Route them through the same
      // pipeline a live peer-op uses — color theme normalization +
      // edge-endpoint defaults — then dispatch to batch listeners.
      // `mode === "live"` carries nothing; the client just continues.
      if (msg.mode === "catch-up" && msg.batches) {
        for (const batch of msg.batches) {
          if (batch.clientId === clientId) continue
          normalizeBatchColorsForLocalTheme(batch)
          normalizeBatchAutoFit(batch, store)
          patchMissingEdgeEndpoints(batch, store)
          for (const cb of batchListeners) cb(batch)
        }
      }
      // 3.2: snapshot welcome carries the current per-room presence
      // map. Replay it through the canvas-harness presence slice so
      // remote cursors / chip entries render immediately instead of
      // waiting for each peer to re-broadcast on their next move.
      // Skip our own clientId — applyRemote with our own id would
      // create a duplicate "self" presence record.
      if (msg.mode === "snapshot" && msg.presence) {
        for (const [cid, state] of Object.entries(msg.presence)) {
          if (cid === clientId) continue
          store.presence.applyRemote(cid as ClientId, state)
        }
      }
      // Signal the outer reconnect loop that we've successfully
      // handshaked — resets the attempt counter and transitions
      // the chrome status pill to "live".
      onWelcome?.(msg.seq)
      return
    }
    if (msg.kind === "peer-op") {
      if (msg.seq > lastServerSeq) lastServerSeq = msg.seq
      // Self-echo is impossible (server excludes sender), but guard.
      if (msg.batch.clientId === clientId) return
      // Re-derive `style.{bg,stroke,text}` from the canonical
      // `_storedColors` field for the local theme. Without this, a
      // dark-mode peer's adapted hex would land in a light-mode
      // store and paint as a dark color on a white canvas (and vice
      // versa). See [color-adapter.ts] for the projection rules.
      normalizeBatchColorsForLocalTheme(msg.batch)
      normalizeBatchAutoFit(msg.batch, store)
      // Defense-in-depth: any attached EdgeEnd without a localOffset
      // would crash canvas-harness's `projectEndToWorld` on
      // `undefined.x`. Server-side `link_to_wire_edge` defaults to
      // node center, but a stray legacy payload could still slip in.
      patchMissingEdgeEndpoints(msg.batch, store)
      for (const cb of batchListeners) cb(msg.batch)
      return
    }
    if (msg.kind === "op-applied") {
      if (msg.seq > lastServerSeq) lastServerSeq = msg.seq
      return
    }
    if (msg.kind === "op-rejected") {
      // The server bounced our op (most commonly: a viewer trying to
      // mutate). The user-facing surface is the read-only banner +
      // any toast wired at a higher level; we just log here. The
      // local store keeps the optimistic mutation — refresh wins.
      console.warn(
        `collab: op rejected client_seq=${msg.client_seq} reason=${msg.reason ?? "(none)"}`,
      )
      return
    }
    if (msg.kind === "presence") {
      if (msg.clientId === clientId) return
      for (const cb of presenceListeners) cb(msg.clientId, msg.state)
      return
    }
    if (msg.kind === "presence-leave") {
      if (msg.clientId === clientId) return
      for (const cb of presenceListeners) cb(msg.clientId, null)
      return
    }
    if (msg.kind === "hello" && msg.clientId !== clientId && lastLocalPresence) {
      send({ kind: "presence", clientId, state: lastLocalPresence })
      return
    }
    if (msg.kind === "research-progress") {
      // UI-only signal for deep-research sub-agent panel (no canvas ops).
      try {
        // Dynamic import avoids circular deps with board feature stores.
        void import("../../lib/research-live-store").then((mod) => {
          if (msg.snapshot) {
            mod.setResearchLiveSnapshot({
              board_id: msg.snapshot.board_id || msg.board_id || "",
              session_id: msg.snapshot.session_id || msg.session_id || "",
              mode: msg.snapshot.mode,
              last_event: msg.snapshot.last_event,
              last_label: msg.snapshot.last_label,
              completed: msg.snapshot.completed,
              failed: msg.snapshot.failed,
              active: msg.snapshot.active,
              nodes_seen: msg.snapshot.nodes_seen,
              agents: (msg.snapshot.agents || []) as mod.ResearchAgentCard[],
              events: msg.snapshot.events || [],
              updated_at: msg.snapshot.updated_at,
            })
          }
          if (msg.event) {
            mod.applyResearchLiveEvent(
              msg.board_id || msg.snapshot?.board_id || "",
              msg.session_id || msg.snapshot?.session_id || "",
              msg.event,
            )
          }
        })
      } catch {
        // ignore
      }
      return
    }
    if (msg.kind === "kick") {
      console.warn(`collab: kicked by server reason=${msg.reason ?? "(none)"}`)
      try {
        ws.close(1000, "kicked")
      } catch {
        // ignore
      }
    }
  })

  ws.addEventListener("close", (e) => {
    if (e.code !== 1000 && e.code !== 1001) {
      console.warn(`collab: ws closed code=${e.code} reason=${e.reason || "(none)"}`)
    }
    notifyWsClose(e.code)
    // Hand the last known seq back to the outer reconnect loop so the
    // next attempt's `since_seq` is accurate. The outer loop also
    // decides whether to schedule a retry based on the close code.
    onClose?.(e.code, lastServerSeq)
  })

  const onPageHide = () => {
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ kind: "presence-leave", clientId }))
    }
  }
  window.addEventListener("pagehide", onPageHide)

  return {
    capabilities: { causalOrdering: true },
    sendBatch(batch: OpBatch) {
      clientSeq += 1
      queueOp(clientSeq, batch)
    },
    sendPresence(patch: PresencePatch) {
      const state: PresenceState = {
        ...(lastLocalPresence ?? ({} as PresenceState)),
        ...patch,
        clientId,
      }
      lastLocalPresence = state
      presenceThrottle.push(state)
    },
    onBatch(cb) {
      batchListeners.add(cb)
      return () => {
        batchListeners.delete(cb)
      }
    },
    onPresence(cb) {
      presenceListeners.add(cb)
      return () => {
        presenceListeners.delete(cb)
      }
    },
    destroy() {
      window.removeEventListener("pagehide", onPageHide)
      // Flush any buffered ops synchronously so a teardown right after
      // an edit (e.g. closing the tab) doesn't drop the user's last
      // action. Cancel the deferred timer first.
      if (coalesceTimer !== null) {
        clearTimeout(coalesceTimer)
        coalesceTimer = null
      }
      flushPendingOps()
      // Same for the presence throttle — drop pending without sending
      // (a `presence-leave` is about to supersede whatever was queued).
      presenceThrottle.cancel()
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ kind: "presence-leave", clientId }))
      }
      try {
        ws.close(1000, "client detached")
      } catch {
        // ignore
      }
      batchListeners.clear()
      presenceListeners.clear()
    },
  }
}
