"""In-process room registry for collab Phase 1a.

A Room is the per-board set of currently-connected sockets. Messages
received on one socket are forwarded to all other sockets in the same
room (pure relay — no sequencing, no DB writes). The room is created
lazily on first join and torn down when the last client leaves.
"""

import asyncio
import json
import logging
import uuid

from collections import deque
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


# Per-room cap on the in-memory ring of recent broadcasts. 500 batches
# covers ~5 minutes of heavy collab activity; reconnects that drift
# further fall back to a full snapshot rebuild (1c.2).
ROOM_BUFFER_CAP = 500


# Max byte size of a single presence state payload (3.2). Defensive cap
# against a malicious / buggy client pushing 100KB of garbage into
# `Room.presence`. 4KB is comfortable for cursor + selection + editing
# + an emoji-laden name.
MAX_PRESENCE_PAYLOAD_BYTES = 4_096


@dataclass
class Client:
    """One connected socket inside a Room.

    `role` is the per-board role copied from the consumed collab ticket
    (`"owner" | "member" | "viewer"`). It governs op-message acceptance
    inside the WS handler — viewers receive everything but their own ops
    are rejected with `op-rejected`. Defaults to `"member"` for back-compat
    with call sites that pre-date Slice 2 of sharing Phase A.

    `app_client_id` records the application-level `clientId` the peer
    self-announced via its first `presence` (or `hello`) frame. Distinct
    from `client_id` which is a server-assigned UUID. We need this to
    clean up `Room.presence` and emit the matching `presence-leave` on
    disconnect — the registry is keyed by the app clientId, not the UUID.
    """

    client_id: str
    user_id: str
    socket: WebSocket
    role: str = "member"
    app_client_id: str | None = None


@dataclass
class Room:
    """Per-board in-memory presence + sequenced op relay.

    `lock` serializes both membership changes and op sequencing so a
    `peer-op` broadcast can never observe a seq earlier than its own
    DB apply. `send` calls inside `broadcast` run outside the lock so
    a slow peer doesn't block ordering for the rest of the room.

    `seq` is the monotonic per-room sequence assigned to every applied
    op. Survives the lifetime of the in-process Room; rooms reset to 0
    on first creation (acceptable for Phase 1b — Phase 1c may pin it
    to a Redis INCR for cross-restart durability).

    `_buffer` is a per-room ring of recent `(seq, batch)` entries used
    for `since_seq` catch-up on reconnect (Phase 1c.2). Capped at
    `ROOM_BUFFER_CAP`; drifted reconnects fall back to snapshot mode.
    Always mutated under `lock`.
    """

    board_id: str
    clients: dict[str, Client] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    seq: int = 0
    _buffer: deque[tuple[int, dict[str, Any]]] = field(
        default_factory=lambda: deque(maxlen=ROOM_BUFFER_CAP),
    )
    # Per-room presence snapshot — populated as each peer sends a
    # `presence` frame, cleared on `presence-leave` / disconnect.
    # Shipped in the welcome handshake so a newly-joining peer doesn't
    # have to wait for existing peers to re-broadcast.
    presence: dict[str, dict[str, Any]] = field(default_factory=dict)

    def next_seq_unlocked(self) -> int:
        """Caller must hold `self.lock`. Assigns the next monotonic seq."""
        self.seq += 1
        return self.seq

    def remember_batch_unlocked(self, seq: int, batch: dict[str, Any]) -> None:
        """Caller must hold `self.lock`. Records a broadcast batch in the ring."""
        self._buffer.append((seq, batch))

    def update_presence_unlocked(
        self, client_id: str, state: dict[str, Any],
    ) -> None:
        """Caller must hold `self.lock`. Upsert a peer's presence state."""
        self.presence[client_id] = state

    def clear_presence_unlocked(self, client_id: str) -> None:
        """Caller must hold `self.lock`. Drop a peer's presence entry."""
        self.presence.pop(client_id, None)

    def presence_snapshot_unlocked(
        self, *, exclude_client_id: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Caller must hold `self.lock`. Dump of presence for the welcome map.

        `exclude_client_id` omits the joining peer's own previous state
        when present (e.g., a re-join after a quick reconnect — the
        client's local state is already correct, no need to echo it).
        """
        if exclude_client_id is None:
            return dict(self.presence)
        return {
            cid: state for cid, state in self.presence.items()
            if cid != exclude_client_id
        }

    def batches_since_unlocked(self, since_seq: int) -> list[dict[str, Any]] | None:
        """Caller must hold `self.lock`. Slice `(since_seq, room.seq]` from the ring.

        Returns the ordered list of batches the joiner needs to catch up,
        or `None` when the requested seq predates the ring's oldest entry
        (caller should fall back to snapshot mode).
        """
        if not self._buffer:
            # Empty ring + since_seq matches current seq → nothing to do (live).
            # Empty ring + since_seq behind current seq → drift past buffer.
            return [] if since_seq >= self.seq else None
        oldest_seq = self._buffer[0][0]
        if since_seq < oldest_seq - 1:
            # Requested resume point is older than what we've kept.
            return None
        return [batch for (s, batch) in self._buffer if s > since_seq]

    async def add(
        self,
        socket: WebSocket,
        user_id: str,
        role: str = "member",
        *,
        max_size: int | None = None,
    ) -> Client | None:
        """Register a connected socket; return the assigned Client.

        When `max_size` is set, the membership check + insertion happen
        atomically under `self.lock` so a race between two concurrent
        joiners can't push the room over its capacity. Returns `None`
        when the room is already at `max_size` (caller should close the
        socket with a `"room-full"` reason).
        """
        async with self.lock:
            if max_size is not None and len(self.clients) >= max_size:
                return None
            client = Client(
                client_id=str(uuid.uuid4()),
                user_id=user_id,
                role=role,
                socket=socket,
            )
            self.clients[client.client_id] = client
        return client

    async def remove(self, client: Client) -> int:
        """Detach a client; return the remaining client count."""
        async with self.lock:
            self.clients.pop(client.client_id, None)
            return len(self.clients)

    async def broadcast(self, raw: str, exclude: Client | None = None) -> None:
        """Forward a raw text frame to every other client in the room."""
        async with self.lock:
            targets = [c for c in self.clients.values() if c is not exclude]
        for c in targets:
            try:
                await c.socket.send_text(raw)
            except Exception:
                # Best-effort: a dead socket will be cleaned up on its
                # own disconnect handler. We don't tear the room down
                # over one failed send.
                logger.debug("collab broadcast send failed", exc_info=True)

    async def kick_user(self, user_id: str, *, reason: str) -> int:
        """Send `kick { reason }` to every socket owned by `user_id`, then close.

        Returns the number of sockets kicked (zero if the user has no
        live sessions in this room). Multi-tab users get every tab
        kicked — we iterate by `user_id`, not by `client_id`. We don't
        pop the clients from `self.clients` here; the per-socket WS
        handler's `finally` block calls `registry.leave` on its own
        when the close lands.
        """
        async with self.lock:
            victims = [c for c in self.clients.values() if c.user_id == user_id]
            # Downgrade to viewer under the lock so any op racing the close
            # is rejected by the `role not in _EDIT_ROLES` check before the
            # socket actually drops — otherwise a revoked user can mutate
            # in the window between the PG role delete and the close landing.
            for c in victims:
                c.role = "viewer"
        frame = json.dumps({"kind": "kick", "reason": reason})
        kicked = 0
        for c in victims:
            try:
                await c.socket.send_text(frame)
            except Exception:
                logger.debug("collab kick frame send failed", exc_info=True)
            try:
                await c.socket.close(code=1000, reason=reason)
            except Exception:
                logger.debug("collab kick socket close failed", exc_info=True)
            kicked += 1
        return kicked


class RoomRegistry:
    """Lazily-created per-board Room instances for one worker process."""

    def __init__(self) -> None:
        """Initialize an empty registry — rooms are created on first join."""
        self._rooms: dict[str, Room] = {}
        self._lock = asyncio.Lock()

    async def join(
        self,
        board_id: str,
        socket: WebSocket,
        user_id: str,
        role: str = "member",
        *,
        max_size: int | None = None,
    ) -> tuple[Room | None, Client | None]:
        """Join (or create) the room for `board_id`.

        Returns `(room, client)` on success. When `max_size` is set and
        the room is already at capacity, returns `(room, None)` — the
        room is preserved (it may still be hosting peers) but this
        socket was not added. Callers should close the socket with a
        `"room-full"` reason on a `None` client.
        """
        async with self._lock:
            room = self._rooms.get(board_id)
            if room is None:
                room = Room(board_id=board_id)
                self._rooms[board_id] = room
        client = await room.add(socket, user_id, role, max_size=max_size)
        return room, client

    def get(self, board_id: str) -> Room | None:
        """Return the live Room for `board_id` or `None` if no one's connected.

        Used by the agent bridge to decide whether to broadcast: when the
        board has no listeners, the bridge skips the broadcast and just
        persists. Reading `self._rooms` synchronously is safe — Python's
        GIL makes single-attribute access atomic, and a stale read just
        means a missed (or extra) broadcast attempt, not data corruption.
        """
        return self._rooms.get(board_id)

    async def leave(self, room: Room, client: Client) -> None:
        """Remove a client; drop the room from the registry if it's now empty."""
        remaining = await room.remove(client)
        if remaining == 0:
            async with self._lock:
                # Re-check under the lock: someone may have re-joined.
                if self._rooms.get(room.board_id) is room and not room.clients:
                    self._rooms.pop(room.board_id, None)
