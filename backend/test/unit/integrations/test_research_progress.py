"""Unit tests for the Redis-backed research-progress mirror.

Covers the multi-worker safety contract: a session recorded on one worker
(in-memory + Redis mirror) is readable on another worker that has no
in-memory copy, via the Redis fallback. Also covers graceful degradation
when Redis is unavailable (behaviour must match the old in-memory-only path).
"""

from __future__ import annotations

import pytest

from topix.integrations import research_progress as rp


class _FakePipe:
    """Minimal pipeline recording set/delete ops for deferred execution."""

    def __init__(self, store: dict[str, str]) -> None:
        self._store = store
        self._ops: list[tuple[str, str, str | None]] = []

    def set(self, key: str, value: str, ex: int | None = None) -> "_FakePipe":
        self._ops.append(("set", key, value))
        return self

    def delete(self, key: str) -> "_FakePipe":
        self._ops.append(("del", key, None))
        return self

    def execute(self) -> list:
        for op, key, value in self._ops:
            if op == "set":
                self._store[key] = value  # type: ignore[assignment]
            elif op == "del":
                self._store.pop(key, None)
        return []


class _FakeRedis:
    """Tiny in-memory Redis stand-in supporting the calls the mirror uses."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def ping(self) -> bool:
        return True

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def delete(self, key: str) -> int:
        return 1 if self.store.pop(key, None) is not None else 0

    def pipeline(self) -> _FakePipe:
        return _FakePipe(self.store)


@pytest.fixture
def redis_mirror(monkeypatch):
    """Inject a FakeRedis and isolate the module-level in-memory state."""
    fake = _FakeRedis()
    monkeypatch.setattr(rp, "_redis_client", fake)
    monkeypatch.setattr(rp, "_redis_unavailable", False)
    rp._sessions.clear()
    rp._board_latest.clear()
    return fake


@pytest.fixture
def no_redis(monkeypatch):
    """Simulate Redis being unavailable (in-memory-only path)."""
    monkeypatch.setattr(rp, "_redis_client", None)
    monkeypatch.setattr(rp, "_redis_unavailable", True)
    rp._sessions.clear()
    rp._board_latest.clear()


def _simulate_other_worker() -> None:
    """Clear the in-memory store to mimic a second worker with no local copy."""
    rp._sessions.clear()
    rp._board_latest.clear()


def test_record_then_read_from_other_worker_via_redis(redis_mirror):
    """A session recorded on worker A is visible to worker B through Redis."""
    rp.track_session("s1", "b1", mode="run")
    rp.record_event("s1", "b1", "workstream_started", label="Trục 1",
                    agent_id="ws-1", role="workstream")
    rp.set_nodes_seen("s1", 5)

    # Worker B has no in-memory copy.
    _simulate_other_worker()

    prog = rp.get_board_progress("b1")
    assert prog is not None
    assert prog.session_id == "s1"
    assert prog.board_id == "b1"
    assert prog.nodes_seen == 5
    assert any(e.event_type == "workstream_started" for e in prog.events)
    assert "ws-1" in prog.agents

    assert rp.get_board_latest_session("b1") == "s1"

    events, cursor = rp.list_events_since("s1", 0)
    assert len(events) >= 2  # planning seed + workstream_started
    assert cursor == len(events)


def test_get_progress_falls_back_to_redis(redis_mirror):
    """get_progress resolves from Redis when the in-memory copy is absent."""
    rp.track_session("s2", "b2", mode="explore")
    _simulate_other_worker()
    prog = rp.get_progress("s2")
    assert prog is not None
    assert prog.mode == "explore"


def test_clear_session_removes_redis_mirror(redis_mirror):
    """clear_session drops the Redis copy even when called from a worker without the in-memory entry."""
    rp.track_session("s3", "b3", mode="run")
    rp.record_event("s3", "b3", "finding_added", label="F1")
    _simulate_other_worker()
    assert rp.get_progress("s3") is not None
    rp.clear_session("s3")
    assert rp.get_progress("s3") is None
    assert rp.get_board_latest_session("b3") is None


def test_recording_worker_still_uses_in_memory(redis_mirror):
    """The hot path on the recording worker must remain in-memory (no Redis read)."""
    rp.track_session("s4", "b4", mode="run")
    # Wipe Redis only — the recording worker should still resolve from memory.
    redis_mirror.store.clear()
    prog = rp.get_progress("s4")
    assert prog is not None
    assert prog.session_id == "s4"


def test_no_redis_does_not_break_writes(no_redis):
    """With Redis unavailable, writes must not raise and reads return in-memory."""
    rp.track_session("s5", "b5", mode="run")
    rp.record_event("s5", "b5", "finding_added", label="F")
    rp.set_nodes_seen("s5", 3)
    prog = rp.get_board_progress("b5")
    assert prog is not None
    assert prog.nodes_seen == 3
    rp.clear_session("s5")
    assert rp.get_progress("s5") is None


def test_no_redis_read_miss_returns_none(no_redis):
    """With Redis down, an in-memory miss yields None/empty without raising."""
    _simulate_other_worker()
    assert rp.get_progress("nope") is None
    assert rp.get_board_progress("no-board") is None
    assert rp.get_board_latest_session("no-board") is None
    events, _ = rp.list_events_since("nope", 0)
    assert events == []


def test_serialize_roundtrip_preserves_events_and_agents(redis_mirror):
    """Round-trip via Redis must keep event order, agent cards, and status flags."""
    rp.track_session("s6", "b6", mode="run")
    rp.record_event("s6", "b6", "workstream_started", label="WS", agent_id="ws-a")
    rp.record_event("s6", "b6", "completed", label="done", agent_id="lead")
    _simulate_other_worker()
    prog = rp.get_board_progress("b6")
    assert prog is not None
    assert prog.completed is True
    assert prog.events[0].event_type == "planning"
    assert prog.events[-1].event_type == "completed"
    assert prog.agents["lead"].status == "done"
