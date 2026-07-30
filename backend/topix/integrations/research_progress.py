"""In-memory research progress for SSE + canvas live panels.

Tracks per-session timelines of structured agent events so:
  - SSE research streams can surface sub-agent activity
  - collab WS can push `research-progress` to open boards
  - GET endpoints can poll the latest agent cards
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid

from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# High-level event types Claude/MCP may emit.
EVENT_TYPES = frozenset({
    "planning",
    "workstream_started",
    "source_found",
    "finding_added",
    "cross_checking",
    "synthesizing",
    "agent_started",
    "agent_progress",
    "agent_done",
    "agent_failed",
    "completed",
    "failed",
    "cancelled",
})


@dataclass
class AgentCard:
    """One logical sub-agent / workstream shown in the live UI."""

    agent_id: str
    role: str = "worker"
    label: str = ""
    status: str = "running"  # pending | running | done | failed
    detail: str = ""
    query: str = ""
    updated_at: float = field(default_factory=time.time)


@dataclass
class ProgressEvent:
    """One timeline row (raw event from MCP or runner)."""

    id: str
    session_id: str
    board_id: str
    event_type: str
    label: str = ""
    agent_id: str | None = None
    role: str | None = None
    detail: str | None = None
    query: str | None = None
    ts: float = field(default_factory=time.time)


@dataclass
class SessionProgress:
    """Progress for one research session_id."""

    board_id: str
    session_id: str
    mode: str = ""
    last_event: str = ""
    last_label: str = ""
    completed: bool = False
    failed: bool = False
    updated_at: float = field(default_factory=time.time)
    nodes_seen: int = 0
    events: list[ProgressEvent] = field(default_factory=list)
    agents: dict[str, AgentCard] = field(default_factory=dict)


_lock = threading.Lock()
_sessions: dict[str, SessionProgress] = {}
# board_id → latest session_id (for canvas open without session in URL)
_board_latest: dict[str, str] = {}
_MAX_EVENTS = 200
_MAX_SESSIONS = 80

# --- Redis mirror (multi-worker safety) -----------------------------------
# The in-memory dicts above are per-process. With >1 Uvicorn worker, a research
# event recorded on worker A is invisible to a GET /research-progress that lands
# on worker B. We mirror every write to Redis and fall back to Redis on an
# in-memory read miss, so any worker can read progress for any session. Redis is
# best-effort: if it is unavailable, behaviour is identical to the old in-memory
# only path (no regression on the recording worker's hot path).
_REDIS_TTL = 24 * 3600
_SESSION_KEY = "research:session:{sid}"
_BOARD_KEY = "research:board:{bid}"
_redis_client: Any = None
_redis_unavailable = False


def _redis() -> Any:
    """Return a shared sync Redis client, or None when Redis is unavailable.

    Tests may bypass config by setting ``_redis_client`` directly and clearing
    ``_redis_unavailable``.
    """
    global _redis_client, _redis_unavailable
    if _redis_unavailable:
        return None
    if _redis_client is not None:
        return _redis_client
    try:
        import redis as _redis_mod

        from topix.config.config import Config

        rc = Config.instance().run.databases.redis
        _redis_client = _redis_mod.Redis(
            host=rc.host,
            port=rc.port,
            db=rc.db,
            password=rc.password.get_secret_value() if rc.password else None,
            decode_responses=True,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
        )
        _redis_client.ping()
    except Exception:
        # Config not bootstrapped (e.g. unit tests) or Redis down → in-memory only.
        _redis_unavailable = True
        _redis_client = None
        logger.debug("research_progress: redis unavailable, in-memory only")
        return None
    return _redis_client


def _serialize_session(prog: SessionProgress) -> str:
    """Serialize a session to a compact JSON string for Redis storage."""
    return json.dumps({
        "board_id": prog.board_id,
        "session_id": prog.session_id,
        "mode": prog.mode,
        "last_event": prog.last_event,
        "last_label": prog.last_label,
        "completed": prog.completed,
        "failed": prog.failed,
        "updated_at": prog.updated_at,
        "nodes_seen": prog.nodes_seen,
        "events": [asdict(e) for e in prog.events],
        "agents": {k: asdict(v) for k, v in prog.agents.items()},
    }, separators=(",", ":"))


def _deserialize_session(raw: str) -> SessionProgress:
    """Rebuild a SessionProgress from its Redis JSON representation."""
    d = json.loads(raw)
    return SessionProgress(
        board_id=d["board_id"],
        session_id=d["session_id"],
        mode=d.get("mode", ""),
        last_event=d.get("last_event", ""),
        last_label=d.get("last_label", ""),
        completed=d.get("completed", False),
        failed=d.get("failed", False),
        updated_at=d.get("updated_at", 0.0),
        nodes_seen=d.get("nodes_seen", 0),
        events=[ProgressEvent(**e) for e in d.get("events", [])],
        agents={k: AgentCard(**v) for k, v in d.get("agents", {}).items()},
    )


def _mirror_session(prog: SessionProgress) -> None:
    """Best-effort write-through of a session to Redis (called outside the lock)."""
    r = _redis()
    if r is None:
        return
    try:
        pipe = r.pipeline()
        pipe.set(_SESSION_KEY.format(sid=prog.session_id), _serialize_session(prog), ex=_REDIS_TTL)
        if prog.board_id:
            pipe.set(_BOARD_KEY.format(bid=prog.board_id), prog.session_id, ex=_REDIS_TTL)
        pipe.execute()
    except Exception:
        logger.debug("research_progress: redis mirror write failed", exc_info=True)


def _mirror_clear(session_id: str, board_id: str | None) -> None:
    """Best-effort removal of a session from Redis."""
    r = _redis()
    if r is None:
        return
    try:
        pipe = r.pipeline()
        pipe.delete(_SESSION_KEY.format(sid=session_id))
        if board_id:
            pipe.delete(_BOARD_KEY.format(bid=board_id))
        pipe.execute()
    except Exception:
        logger.debug("research_progress: redis mirror clear failed", exc_info=True)


def _load_session(session_id: str) -> SessionProgress | None:
    """Load a session from Redis on an in-memory miss (best-effort)."""
    r = _redis()
    if r is None:
        return None
    try:
        raw = r.get(_SESSION_KEY.format(sid=session_id))
        if not raw:
            return None
        return _deserialize_session(raw)
    except Exception:
        logger.debug("research_progress: redis load failed", exc_info=True)
        return None


def _load_board_sid(board_id: str) -> str | None:
    """Resolve the latest session_id for a board from Redis on an in-memory miss."""
    r = _redis()
    if r is None:
        return None
    try:
        return r.get(_BOARD_KEY.format(bid=board_id)) or None
    except Exception:
        return None


def track_session(session_id: str, board_id: str, mode: str = "") -> None:
    """Register a session as in-flight and seed a planning agent card."""
    with _lock:
        prog = SessionProgress(
            board_id=board_id,
            session_id=session_id,
            mode=mode,
        )
        lead = AgentCard(
            agent_id="lead",
            role="lead",
            label=f"Lead research ({mode or 'run'})",
            status="running",
            detail="Starting…",
        )
        prog.agents["lead"] = lead
        seed = ProgressEvent(
            id=str(uuid.uuid4())[:12],
            session_id=session_id,
            board_id=board_id,
            event_type="planning",
            label=f"Starting research mode={mode or '?'}",
            agent_id="lead",
            role="lead",
        )
        prog.events.append(seed)
        prog.last_event = "planning"
        prog.last_label = seed.label
        _sessions[session_id] = prog
        if board_id:
            _board_latest[board_id] = session_id
        _trim_sessions_unlocked()
    _mirror_session(prog)


def record_event(
    session_id: str,
    board_id: str,
    event_type: str,
    label: str | None = None,
    *,
    agent_id: str | None = None,
    role: str | None = None,
    detail: str | None = None,
    query: str | None = None,
) -> ProgressEvent:
    """Record a research event and update agent cards. Returns the stored event."""
    et = (event_type or "agent_progress").strip()
    lbl = (label or "").strip()[:200]
    aid = (agent_id or "").strip() or _infer_agent_id(et, lbl, role)
    role_s = (role or "").strip() or _infer_role(et, aid)
    det = (detail or "").strip()[:500] if detail else ""
    qry = (query or "").strip()[:300] if query else ""

    with _lock:
        prog = _sessions.get(session_id)
        if prog is None:
            prog = SessionProgress(board_id=board_id, session_id=session_id)
            _sessions[session_id] = prog
        if board_id:
            prog.board_id = board_id
            _board_latest[board_id] = session_id
        prog.last_event = et
        prog.last_label = lbl
        prog.updated_at = time.time()
        if et == "completed":
            prog.completed = True
            # mark remaining running agents done
            for card in prog.agents.values():
                if card.status == "running":
                    card.status = "done"
                    card.updated_at = prog.updated_at
        if et in ("failed", "cancelled"):
            prog.failed = True
            for card in prog.agents.values():
                if card.status == "running":
                    card.status = "failed"
                    card.updated_at = prog.updated_at

        ev = ProgressEvent(
            id=str(uuid.uuid4())[:12],
            session_id=session_id,
            board_id=prog.board_id,
            event_type=et,
            label=lbl,
            agent_id=aid,
            role=role_s,
            detail=det or None,
            query=qry or None,
        )
        prog.events.append(ev)
        if len(prog.events) > _MAX_EVENTS:
            prog.events = prog.events[-_MAX_EVENTS:]

        _upsert_agent_unlocked(prog, ev)
    _mirror_session(prog)
    return ev


def set_nodes_seen(session_id: str, count: int) -> None:
    """Update latest known node count for the session's board."""
    changed = False
    with _lock:
        prog = _sessions.get(session_id)
        if prog is None:
            return
        if count > prog.nodes_seen:
            prog.nodes_seen = count
            prog.updated_at = time.time()
            changed = True
    if changed:
        _mirror_session(prog)


def get_progress(session_id: str) -> SessionProgress | None:
    """Return a deep-enough copy of session progress or None."""
    with _lock:
        prog = _sessions.get(session_id)
        if prog is not None:
            return _clone_unlocked(prog)
    # In-memory miss (e.g. a different worker) → fall back to Redis.
    return _load_session(session_id)


def get_board_latest_session(board_id: str) -> str | None:
    """Return latest session_id for a board, if any."""
    with _lock:
        sid = _board_latest.get(board_id)
    if sid:
        return sid
    return _load_board_sid(board_id)


def get_board_progress(board_id: str, session_id: str | None = None) -> SessionProgress | None:
    """Resolve progress by session or board's latest session."""
    with _lock:
        sid = session_id or _board_latest.get(board_id)
    if not sid and not session_id:
        # No in-memory mapping → try Redis for the board's latest session.
        sid = _load_board_sid(board_id)
    if not sid:
        return None
    with _lock:
        prog = _sessions.get(sid)
        if prog is not None:
            if board_id and prog.board_id and prog.board_id != board_id:
                # allow empty board_id on old records
                pass
            return _clone_unlocked(prog)
    # In-memory miss → fall back to Redis.
    prog = _load_session(sid)
    if prog is None:
        return None
    if board_id and prog.board_id and prog.board_id != board_id:
        pass
    return prog


def list_events_since(session_id: str, after_index: int = 0) -> tuple[list[ProgressEvent], int]:
    """Return events with index >= after_index and the next cursor."""
    with _lock:
        prog = _sessions.get(session_id)
        if prog is not None:
            events = list(prog.events[after_index:])
            return events, len(prog.events)
    prog = _load_session(session_id)
    if prog is None:
        return [], after_index
    events = list(prog.events[after_index:])
    return events, len(prog.events)


def snapshot_dict(prog: SessionProgress) -> dict[str, Any]:
    """Serialize session progress for JSON/SSE/WS."""
    agents = sorted(
        (asdict(a) for a in prog.agents.values()),
        key=lambda a: (0 if a.get("status") == "running" else 1, a.get("updated_at") or 0),
    )
    events = [asdict(e) for e in prog.events[-80:]]
    return {
        "board_id": prog.board_id,
        "session_id": prog.session_id,
        "mode": prog.mode,
        "last_event": prog.last_event,
        "last_label": prog.last_label,
        "completed": prog.completed,
        "failed": prog.failed,
        "updated_at": prog.updated_at,
        "nodes_seen": prog.nodes_seen,
        "agents": agents,
        "events": events,
        "event_count": len(prog.events),
    }


def clear_session(session_id: str) -> None:
    """Drop session tracking."""
    with _lock:
        prog = _sessions.pop(session_id, None)
        board_id = prog.board_id if prog else None
        if prog and _board_latest.get(prog.board_id) == session_id:
            _board_latest.pop(prog.board_id, None)
    if board_id is None:
        # In-memory miss (e.g. a different worker) — recover the board_id from
        # Redis so the board→session mapping key is cleared too.
        loaded = _load_session(session_id)
        board_id = loaded.board_id if loaded else None
    _mirror_clear(session_id, board_id)


def _clone_unlocked(prog: SessionProgress) -> SessionProgress:
    return SessionProgress(
        board_id=prog.board_id,
        session_id=prog.session_id,
        mode=prog.mode,
        last_event=prog.last_event,
        last_label=prog.last_label,
        completed=prog.completed,
        failed=prog.failed,
        updated_at=prog.updated_at,
        nodes_seen=prog.nodes_seen,
        events=list(prog.events),
        agents={k: AgentCard(**asdict(v)) for k, v in prog.agents.items()},
    )


def _upsert_agent_unlocked(prog: SessionProgress, ev: ProgressEvent) -> None:
    aid = ev.agent_id or "lead"
    card = prog.agents.get(aid)
    if card is None:
        card = AgentCard(
            agent_id=aid,
            role=ev.role or "worker",
            label=ev.label or aid,
            status="running",
        )
        prog.agents[aid] = card
    else:
        if ev.role:
            card.role = ev.role
        if ev.label:
            card.label = ev.label
    if ev.detail:
        card.detail = ev.detail
    if ev.query:
        card.query = ev.query
    # Map event type → card status
    et = ev.event_type
    if et in ("completed", "agent_done", "synthesizing") and aid == "lead" and et == "completed":
        card.status = "done"
    elif et == "agent_done":
        card.status = "done"
    elif et in ("failed", "cancelled", "agent_failed"):
        card.status = "failed" if et != "cancelled" else "failed"
    elif et in (
        "planning", "workstream_started", "source_found", "finding_added",
        "cross_checking", "agent_started", "agent_progress", "synthesizing",
    ):
        if not prog.completed and not prog.failed:
            card.status = "running"
        if et == "synthesizing" and aid == "lead":
            card.detail = ev.label or "Synthesizing graph…"
        if et == "source_found" and not card.detail:
            card.detail = ev.label or "Source found"
    card.updated_at = time.time()

    # Auto lead detail mirror
    if aid != "lead" and "lead" in prog.agents and not prog.completed:
        lead = prog.agents["lead"]
        lead.detail = f"{card.role or 'agent'}: {card.label or card.detail or et}"
        lead.updated_at = card.updated_at


def _infer_agent_id(event_type: str, label: str, role: str | None) -> str:
    if role:
        slug = "".join(c if c.isalnum() else "-" for c in role.lower())[:32]
        return slug or "worker"
    if event_type in ("planning", "synthesizing", "completed", "failed", "cancelled"):
        return "lead"
    if event_type == "cross_checking":
        return "critique"
    if event_type == "workstream_started" and label:
        slug = "".join(c if c.isalnum() else "-" for c in label.lower())[:40]
        return f"ws-{slug}" or "workstream"
    if event_type in ("source_found", "finding_added"):
        return "collector"
    return "worker"


def _infer_role(event_type: str, agent_id: str) -> str:
    if agent_id == "lead":
        return "lead"
    if agent_id == "critique" or event_type == "cross_checking":
        return "critique"
    if agent_id.startswith("ws-") or event_type == "workstream_started":
        return "workstream"
    if event_type in ("source_found", "finding_added") or agent_id == "collector":
        return "collector"
    if event_type == "synthesizing":
        return "writer"
    return "worker"


def _trim_sessions_unlocked() -> None:
    if len(_sessions) <= _MAX_SESSIONS:
        return
    # Drop oldest completed/failed first, then oldest by updated_at
    items = sorted(
        _sessions.items(),
        key=lambda kv: (0 if (kv[1].completed or kv[1].failed) else 1, kv[1].updated_at),
    )
    drop_n = len(_sessions) - _MAX_SESSIONS
    for sid, prog in items[:drop_n]:
        _sessions.pop(sid, None)
        if _board_latest.get(prog.board_id) == sid:
            _board_latest.pop(prog.board_id, None)
