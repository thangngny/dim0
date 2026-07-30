import { useEffect, useState } from "react"
import { apiFetch } from "@/api"
import {
  clearResearchLive,
  getResearchLiveSnapshot,
  setResearchLiveSnapshot,
  subscribeResearchLive,
  type ResearchAgentCard,
  type ResearchLiveSnapshot,
} from "../lib/research-live-store"


type ResearchLivePanelProps = {
  boardId: string
  /** When true, panel starts expanded (research handoff). */
  forceOpen?: boolean
}


const ROLE_ICON: Record<string, string> = {
  lead: "🧠",
  workstream: "🔍",
  collector: "🌐",
  critique: "⚠️",
  writer: "✍️",
  worker: "⚙️",
}


/**
 * Floating panel showing live deep-research sub-agent cards on the canvas.
 */
export function ResearchLivePanel({ boardId, forceOpen }: ResearchLivePanelProps) {
  const [snap, setSnap] = useState<ResearchLiveSnapshot | null>(getResearchLiveSnapshot)
  const [collapsed, setCollapsed] = useState(false)

  useEffect(() => {
    return subscribeResearchLive(() => {
      setSnap(getResearchLiveSnapshot())
    })
  }, [])

  // Poll JWT endpoint while a run may be active (WS is best-effort).
  useEffect(() => {
    if (!boardId) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null

    const tick = async () => {
      try {
        const data = await apiFetch<{ data?: ResearchLiveSnapshot } & ResearchLiveSnapshot>({
          path: `/boards/${boardId}/research-progress`,
          method: "GET",
        })
        if (cancelled) return
        const body = ((data as { data?: ResearchLiveSnapshot }).data ?? data) as ResearchLiveSnapshot
        if (body && (body.session_id || (body.agents && body.agents.length))) {
          setResearchLiveSnapshot({
            board_id: body.board_id || boardId,
            session_id: body.session_id,
            mode: body.mode,
            last_event: body.last_event,
            last_label: body.last_label,
            completed: body.completed,
            failed: body.failed,
            active: body.active,
            nodes_seen: body.nodes_seen,
            agents: (body.agents || []) as ResearchAgentCard[],
            events: body.events || [],
            updated_at: body.updated_at,
          })
        } else {
          // New board has no active research — drop the previous board's
          // stale snapshot so its agent cards don't render here.
          clearResearchLive()
        }
        const active = body?.active === true || (
          body && !body.completed && !body.failed && (body.agents || []).some(
            (a: ResearchAgentCard) => a.status === "running",
          )
        )
        timer = setTimeout(tick, active ? 2500 : 8000)
      } catch {
        timer = setTimeout(tick, 10000)
      }
    }
    void tick()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [boardId])

  useEffect(() => {
    return () => {
      // Keep last snapshot for handoff; only clear if board changes handled above.
    }
  }, [boardId])

  const agents = snap?.agents || []
  const running = agents.filter((a) => a.status === "running").length
  // Show when we have agent cards, or handoff just opened while poll is pending.
  const show = agents.length > 0 || (forceOpen && Boolean(snap?.active))

  if (!show) return null

  const title = snap?.completed
    ? "Research xong"
    : snap?.failed
      ? "Research lỗi"
      : running > 0
        ? `Research live · ${running} agent`
        : "Research live"

  return (
    <div className="absolute bottom-24 right-3 z-[54] w-[min(340px,calc(100vw-1.5rem))] pointer-events-auto">
      <div className="rounded-xl border border-violet-500/30 bg-card/95 backdrop-blur-md shadow-xl overflow-hidden">
        <button
          type="button"
          className="w-full flex items-center justify-between gap-2 px-3 py-2 text-left hover:bg-sidebar/60"
          onClick={() => setCollapsed((c) => !c)}
        >
          <span className="text-sm font-medium text-foreground truncate">
            🤖 {title}
          </span>
          <span className="text-xs text-muted-foreground shrink-0">
            {collapsed ? "mở" : "thu"}
          </span>
        </button>
        {!collapsed && (
          <div className="border-t border-border/60 max-h-[min(42vh,360px)] overflow-y-auto">
            {agents.length === 0 && (
              <p className="px-3 py-2 text-xs text-muted-foreground">
                Đang chờ sub-agent report…
              </p>
            )}
            <ul className="p-2 space-y-1.5">
              {agents.map((a) => (
                <li
                  key={a.agent_id}
                  className={
                    "rounded-lg border px-2.5 py-1.5 text-xs " +
                    (a.status === "running"
                      ? "border-blue-500/40 bg-blue-500/5"
                      : a.status === "failed"
                        ? "border-red-500/40 bg-red-500/5"
                        : "border-border/70 bg-background/50")
                  }
                >
                  <div className="flex items-center gap-1.5">
                    <span className="text-[10px] uppercase tracking-wide rounded-full px-1.5 py-0.5 bg-violet-500/15 text-violet-300 font-semibold">
                      {ROLE_ICON[a.role] || ROLE_ICON.worker} {a.role}
                    </span>
                    <span className="font-medium text-foreground truncate flex-1">
                      {a.label}
                    </span>
                    <span
                      className={
                        "uppercase text-[10px] font-semibold " +
                        (a.status === "running"
                          ? "text-blue-400"
                          : a.status === "failed"
                            ? "text-red-400"
                            : "text-emerald-400")
                      }
                    >
                      {a.status}
                    </span>
                  </div>
                  {a.detail ? (
                    <p className="mt-0.5 text-muted-foreground leading-snug">
                      {a.detail}
                    </p>
                  ) : null}
                  {a.query ? (
                    <p className="mt-0.5 font-mono text-[11px] text-sky-400/90 truncate" title={a.query}>
                      🔎 {a.query}
                    </p>
                  ) : null}
                </li>
              ))}
            </ul>
            {snap?.last_label ? (
              <p className="px-3 pb-2 text-[11px] text-muted-foreground border-t border-border/40 pt-1.5">
                {snap.last_event}: {snap.last_label}
              </p>
            ) : null}
            {(snap?.completed || snap?.failed) && (
              <div className="px-3 pb-2">
                <button
                  type="button"
                  className="text-[11px] text-muted-foreground hover:text-foreground"
                  onClick={() => clearResearchLive()}
                >
                  Ẩn panel
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
