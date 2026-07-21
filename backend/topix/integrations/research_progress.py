"""In-memory research progress for early SSE completion signals.

Claude often writes the full graph via MCP then keeps talking for minutes.
We record research_event(completed) and board node-count snapshots so the
runner can emit `done` without waiting for the CLI process to exit.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


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


_lock = threading.Lock()
_sessions: dict[str, SessionProgress] = {}


def track_session(session_id: str, board_id: str, mode: str = "") -> None:
    """Register a session as in-flight."""
    with _lock:
        _sessions[session_id] = SessionProgress(
            board_id=board_id,
            session_id=session_id,
            mode=mode,
        )


def record_event(
    session_id: str,
    board_id: str,
    event_type: str,
    label: str | None = None,
) -> None:
    """Record a research_event from MCP."""
    with _lock:
        prog = _sessions.get(session_id)
        if prog is None:
            prog = SessionProgress(board_id=board_id, session_id=session_id)
            _sessions[session_id] = prog
        prog.board_id = board_id or prog.board_id
        prog.last_event = event_type
        prog.last_label = label or ""
        prog.updated_at = time.time()
        if event_type == "completed":
            prog.completed = True
        if event_type in ("failed", "cancelled"):
            prog.failed = True


def set_nodes_seen(session_id: str, count: int) -> None:
    """Update latest known node count for the session's board."""
    with _lock:
        prog = _sessions.get(session_id)
        if prog is None:
            return
        if count > prog.nodes_seen:
            prog.nodes_seen = count
            prog.updated_at = time.time()


def get_progress(session_id: str) -> SessionProgress | None:
    """Return progress snapshot or None."""
    with _lock:
        prog = _sessions.get(session_id)
        if prog is None:
            return None
        # copy lightweight
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
        )


def clear_session(session_id: str) -> None:
    """Drop session tracking."""
    with _lock:
        _sessions.pop(session_id, None)
