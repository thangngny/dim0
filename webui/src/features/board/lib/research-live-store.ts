/**
 * In-memory store for deep-research live sub-agent progress on the canvas.
 * Fed by collab WS `research-progress` frames + HTTP poll.
 */

export type ResearchAgentStatus = "pending" | "running" | "done" | "failed"


export type ResearchAgentCard = {
  agent_id: string
  role: string
  label: string
  status: ResearchAgentStatus
  detail: string
  query: string
  updated_at?: number
}


export type ResearchLiveEvent = {
  id?: string
  event_type: string
  label?: string
  agent_id?: string
  role?: string
  detail?: string
  query?: string
  ts?: number
}


export type ResearchLiveSnapshot = {
  board_id: string
  session_id: string
  mode?: string
  last_event?: string
  last_label?: string
  completed?: boolean
  failed?: boolean
  active?: boolean
  nodes_seen?: number
  agents: ResearchAgentCard[]
  events: ResearchLiveEvent[]
  updated_at?: number
}


type Listener = () => void


let snapshot: ResearchLiveSnapshot | null = null
const listeners = new Set<Listener>()


/**
 * Subscribe to research-live snapshot changes. Returns unsubscribe.
 */
export function subscribeResearchLive(listener: Listener): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}


/**
 * Current snapshot (or null if none).
 */
export function getResearchLiveSnapshot(): ResearchLiveSnapshot | null {
  return snapshot
}


function emit(): void {
  for (const l of listeners) {
    try {
      l()
    } catch {
      // ignore subscriber errors
    }
  }
}


/**
 * Replace full snapshot from poll/WS.
 */
export function setResearchLiveSnapshot(next: ResearchLiveSnapshot | null): void {
  snapshot = next
  emit()
}


/**
 * Merge a single event into the current snapshot (create if needed).
 */
export function applyResearchLiveEvent(
  boardId: string,
  sessionId: string,
  event: ResearchLiveEvent,
  partialSnapshot?: Partial<ResearchLiveSnapshot>,
): void {
  const base: ResearchLiveSnapshot = snapshot && snapshot.board_id === boardId
    ? { ...snapshot, agents: [...snapshot.agents], events: [...snapshot.events] }
    : {
        board_id: boardId,
        session_id: sessionId,
        agents: [],
        events: [],
        active: true,
      }

  base.session_id = sessionId || base.session_id
  if (partialSnapshot) {
    Object.assign(base, partialSnapshot)
  }

  const agentId = event.agent_id || "lead"
  const role = event.role || "worker"
  let status: ResearchAgentStatus = "running"
  if (event.event_type === "agent_done" || event.event_type === "completed") {
    status = "done"
  }
  if (["failed", "cancelled", "agent_failed"].includes(event.event_type || "")) {
    status = "failed"
  }

  const idx = base.agents.findIndex((a) => a.agent_id === agentId)
  const prev = idx >= 0 ? base.agents[idx] : undefined
  const card: ResearchAgentCard = {
    agent_id: agentId,
    role,
    label: event.label || prev?.label || agentId,
    status:
      status === "running" && prev?.status === "done" ? "done" : status,
    detail: event.detail || prev?.detail || "",
    query: event.query || prev?.query || "",
    updated_at: event.ts || Date.now() / 1000,
  }
  if (idx >= 0) base.agents[idx] = card
  else base.agents.push(card)

  if (event.event_type === "completed") {
    base.completed = true
    base.active = false
    base.agents = base.agents.map((a) =>
      a.status === "running" ? { ...a, status: "done" as const } : a,
    )
  }
  if (event.event_type === "failed" || event.event_type === "cancelled") {
    base.failed = true
    base.active = false
  }

  const eid = event.id || `${agentId}-${event.event_type}-${event.ts}-${event.label}`
  if (!base.events.some((e) => (e.id || "") === eid)) {
    base.events = [{ ...event, id: eid }, ...base.events].slice(0, 80)
  }

  base.last_event = event.event_type
  base.last_label = event.label || base.last_label
  base.updated_at = Date.now() / 1000
  snapshot = base
  emit()
}


/**
 * Clear snapshot when leaving board / run finished long ago.
 */
export function clearResearchLive(): void {
  snapshot = null
  emit()
}
