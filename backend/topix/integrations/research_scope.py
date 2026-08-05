"""Active expand-scope sessions for server-side write guards.

While an expand research run is active on a board, updates/deletes are
restricted to focus nodes and nodes created during that session. Creates
are allowed and newly created ids join the allowlist.
"""

from __future__ import annotations

import threading
import time

from dataclasses import dataclass, field


@dataclass
class ExpandScope:
    """In-memory expand scope for one board research session."""

    board_id: str
    session_id: str
    focus_ids: set[str] = field(default_factory=set)
    allowed_ids: set[str] = field(default_factory=set)
    max_new_nodes: int = 20
    created_count: int = 0
    expires_at: float = 0.0

    def is_expired(self) -> bool:
        return time.time() >= self.expires_at

    def allows_mutate(self, node_id: str) -> bool:
        """True if update/delete is allowed for this node."""
        return node_id in self.allowed_ids or node_id in self.focus_ids

    def register_created(self, node_ids: list[str]) -> None:
        """Track newly created nodes under this expand session.

        Counts are reserved up-front by ``assert_can_create`` (under the
        module lock) so this only records the ids into the allowlist.
        """
        for nid in node_ids:
            self.allowed_ids.add(nid)

    def can_create(self, n: int = 1) -> bool:
        return self.created_count + n <= self.max_new_nodes


_lock = threading.Lock()
# board_id -> active expand scope
_scopes: dict[str, ExpandScope] = {}


def begin_expand_scope(
    board_id: str,
    session_id: str,
    focus_ids: list[str],
    *,
    max_new_nodes: int = 20,
    ttl_sec: float = 3600.0,
) -> ExpandScope:
    """Start or replace expand scope for a board."""
    scope = ExpandScope(
        board_id=board_id,
        session_id=session_id,
        focus_ids=set(focus_ids),
        allowed_ids=set(focus_ids),
        max_new_nodes=max_new_nodes,
        expires_at=time.time() + ttl_sec,
    )
    with _lock:
        _scopes[board_id] = scope
    return scope


def end_scope(board_id: str, session_id: str | None = None) -> None:
    """Clear expand scope for board (optionally only matching session)."""
    with _lock:
        cur = _scopes.get(board_id)
        if not cur:
            return
        if session_id and cur.session_id != session_id:
            return
        del _scopes[board_id]


def get_scope(board_id: str) -> ExpandScope | None:
    """Return active non-expired expand scope or None."""
    with _lock:
        scope = _scopes.get(board_id)
        if not scope:
            return None
        if scope.is_expired():
            del _scopes[board_id]
            return None
        return scope


def assert_can_mutate(board_id: str, node_id: str) -> None:
    """Raise ValueError if expand scope forbids mutating node_id."""
    scope = get_scope(board_id)
    if scope is None:
        return
    if not scope.allows_mutate(node_id):
        raise ValueError(
            f"Expand scope active: node '{node_id}' is outside focus "
            f"(session={scope.session_id}). Only focus/created nodes may be updated/deleted."
        )


def assert_can_create(board_id: str, count: int) -> None:
    """Reserve ``count`` create slots atomically; raise if over budget.

    The reservation (``created_count += count``) happens under the module
    lock so two concurrent batches cannot both pass ``can_create`` against
    a stale count and together exceed ``max_new_nodes``. ``note_created``
    then only records the resulting ids — it no longer increments.
    """
    with _lock:
        scope = _scopes.get(board_id)
        if not scope or scope.is_expired():
            if scope:
                del _scopes[board_id]
            return
        if not scope.can_create(count):
            raise ValueError(
                f"Expand scope max_new_nodes={scope.max_new_nodes} exceeded "
                f"(already created {scope.created_count}, requested +{count})."
            )
        scope.created_count += count


def note_created(board_id: str, node_ids: list[str]) -> None:
    """Register created node ids into active expand scope."""
    scope = get_scope(board_id)
    if scope is None:
        return
    scope.register_created(node_ids)
