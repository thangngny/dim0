"""Collaboration router — ticket mint endpoint + WebSocket session.

Per-board WebSocket session:

  1. Ticket exchange validates `(user_id, board_id)` and looks up the
     user's effective role on the board (owner / member / viewer).
     The role is stamped on the ticket and propagated to the WS
     handler via the consumed payload.
  2. On accept, a capacity check looks up the owner's plan and rejects
     joiners that would push the room over its plan-tier cap (close
     code 4429).
  3. `welcome { seq, snapshot }` is sent inside the room's lock so a
     racing `peer-op` cannot precede it on this socket.
  4. Incoming `{ kind: "op" }` from a viewer is rejected with
     `op-rejected { client_seq, reason: "read-only" }`. From an owner
     or member, the op is sequenced under the lock, applied to the
     GraphStore, broadcast as `peer-op` to other clients, and acked
     to the sender with `op-applied`.
  5. Other message kinds (presence, hello, presence-leave) still relay
     verbatim — those graduate to `peer-*` shapes in Phase 3.
"""

import json
import logging

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, WebSocket, WebSocketDisconnect, status

from topix.api.utils.decorators import with_standard_response
from topix.api.utils.security import get_current_user_uid
from topix.collab.apply_ops import apply_batch
from topix.collab.capacity import get_room_cap_for_board
from topix.collab.room import MAX_PRESENCE_PAYLOAD_BYTES, Client, Room, RoomRegistry
from topix.collab.snapshot import read_snapshot_payload
from topix.collab.tickets import consume_ticket, mint_ticket
from topix.store.graph import GraphStore

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/boards",
    tags=["collab"],
    responses={404: {"description": "Not found"}},
)


# Close codes — 4000-4999 range is reserved for app-defined per RFC 6455.
WS_INVALID_TICKET = 4401
WS_BOARD_MISMATCH = 4403
WS_ROOM_FULL = 4429

_ACCESS_ROLES = frozenset({"owner", "member", "viewer"})
_EDIT_ROLES = frozenset({"owner", "member"})


@router.post("/{graph_id}/collab/ticket/", include_in_schema=False)
@router.post("/{graph_id}/collab/ticket")
@with_standard_response
async def mint_collab_ticket(
    response: Response,
    request: Request,
    graph_id: Annotated[str, Path(description="Graph ID")],
    user_id: Annotated[str, Depends(get_current_user_uid)],
):
    """Mint a short-lived single-use ticket the client exchanges on WS upgrade.

    Resolves the user's effective role on the board inline (replaces
    the previous `verify_board_member` dep — we need the role anyway,
    and viewers must be allowed through). 404 for users with no role
    so we don't leak board existence.
    """
    graph_store: GraphStore = request.app.graph_store
    role = await graph_store.get_graph_role(graph_uid=graph_id, user_uid=user_id)
    if role not in _ACCESS_ROLES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Board not found")
    token = await mint_ticket(
        request.app.redis_store, user_id=user_id, board_id=graph_id, role=role,
    )
    return {"ticket": token, "expires_in": 30, "role": role}


@router.websocket("/{graph_id}/collab")
async def collab_ws(  # noqa: C901 — accept/auth/join/welcome/loop is a single state machine, splitting hurts readability
    websocket: WebSocket,
    graph_id: Annotated[str, Path(description="Graph ID")],
    ticket: Annotated[str | None, Query(description="One-shot auth ticket")] = None,
    since_seq: Annotated[
        int | None,
        Query(
            description="Highest seq the client has previously observed; "
            "set on reconnect for catch-up mode. Omit on first connect.",
            ge=0,
        ),
    ] = None,
    root_id: Annotated[
        str | None,
        Query(
            description="Folder scope of the client's current view. Mirrors "
            "the REST `getBoard` query: when set, the welcome snapshot is "
            "scoped to nodes under this parent so the WS hand-off doesn't "
            "replace a folder view with the whole-board contents.",
        ),
    ] = None,
):
    """Per-board relay socket.

    Authenticates via a one-shot Redis-backed ticket, then forwards
    every text frame to other clients in the room. Self-echo
    suppression is handled by excluding the sender from broadcast.
    """
    if not ticket:
        await websocket.close(code=WS_INVALID_TICKET, reason="missing ticket")
        return

    payload = await consume_ticket(websocket.app.redis_store, ticket)
    if not payload:
        await websocket.close(code=WS_INVALID_TICKET, reason="invalid or expired ticket")
        return
    if payload.get("board_id") != graph_id:
        await websocket.close(code=WS_BOARD_MISMATCH, reason="ticket board mismatch")
        return

    user_id: str = payload["user_id"]
    role: str = payload.get("role", "member")

    graph_store = websocket.app.graph_store
    user_billing_store = websocket.app.user_billing_store
    registry: RoomRegistry = websocket.app.collab_rooms

    # Owner's plan caps the room. Done BEFORE accept() so the rejected
    # joiner sees an HTTP 403 on the upgrade rather than an immediate
    # WS close (cleaner UX for the "room full" error).
    try:
        max_size = await get_room_cap_for_board(
            graph_store=graph_store,
            user_billing_store=user_billing_store,
            board_uid=graph_id,
        )
    except Exception:
        logger.exception("collab capacity lookup failed board=%s", graph_id)
        max_size = None  # fail open — log + allow rather than block on infra hiccup

    await websocket.accept()

    room, client = await registry.join(
        graph_id, websocket, user_id, role=role, max_size=max_size,
    )
    if client is None:
        logger.info(
            "collab room-full board=%s user=%s (cap=%s)",
            graph_id, user_id, max_size,
        )
        await websocket.close(code=WS_ROOM_FULL, reason="room-full")
        return
    logger.info(
        "collab join board=%s user=%s role=%s client=%s",
        graph_id, user_id, role, client.client_id,
    )

    # Welcome handshake — dispatch under the room lock so a racing
    # op-handler can't queue a `peer-op` on this socket before the
    # welcome lands. Phase 1c.2: three modes based on `since_seq`:
    #
    #   - `None` (first connect)              → snapshot
    #   - >= room.seq (already current)       → live (no payload)
    #   - in buffer range (`since_seq < seq`) → catch-up batches
    #   - past buffer floor (drifted)         → snapshot fallback
    try:
        await _send_welcome(
            websocket=websocket,
            room=room,
            client_id=client.client_id,
            graph_store=graph_store,
            board_id=graph_id,
            root_id=root_id,
            since_seq=since_seq,
        )
    except Exception:
        logger.exception("collab welcome send failed board=%s", graph_id)
        await registry.leave(room, client)
        return

    try:
        while True:
            raw = await websocket.receive_text()
            await _handle_message(
                websocket=websocket,
                raw=raw,
                graph_store=graph_store,
                room=room,
                client=client,
                board_id=graph_id,
                user_id=user_id,
            )
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("collab socket error board=%s client=%s", graph_id, client.client_id)
    finally:
        # 3.2: emit a synthetic `peer-presence-leave` to remaining peers
        # so cursors / chip entries don't ghost when a socket dies
        # without an explicit leave frame. The client itself sends one
        # on `pagehide` for clean shutdowns; this covers crashes,
        # network drops, and tab kills. Skipped when this socket never
        # announced its `app_client_id` (it never showed up in the
        # presence registry).
        if client.app_client_id is not None:
            async with room.lock:
                had_presence = client.app_client_id in room.presence
                room.clear_presence_unlocked(client.app_client_id)
            if had_presence:
                leave_frame = json.dumps({
                    "kind": "presence-leave",
                    "clientId": client.app_client_id,
                })
                await room.broadcast(leave_frame, exclude=client)
        await registry.leave(room, client)
        logger.info("collab leave board=%s client=%s", graph_id, client.client_id)


async def _send_welcome(
    *,
    websocket: WebSocket,
    room: Room,
    client_id: str,
    graph_store,
    board_id: str,
    root_id: str | None,
    since_seq: int | None,
) -> None:
    """Send the welcome frame appropriate to the client's `since_seq`.

    Acquires `room.lock` for the duration so a peer-op broadcast can't
    interleave between the seq read and the welcome send — the joining
    client never observes a seq earlier than its welcome's seq.

    Snapshot mode carries the current `presence` map so a freshly-
    joining peer (or one rebuilding after a long drift) sees existing
    peers immediately, instead of waiting for them to re-broadcast.
    Catch-up + live skip it — those peers still have their local
    presence state untouched.
    """
    async with room.lock:
        seq = room.seq
        # First connect → full snapshot.
        if since_seq is None:
            snapshot = await read_snapshot_payload(
                graph_store=graph_store, board_id=board_id, root_id=root_id,
            )
            await websocket.send_json({
                "kind": "welcome",
                "mode": "snapshot",
                "seq": seq,
                "snapshot": snapshot,
                "presence": room.presence_snapshot_unlocked(
                    exclude_client_id=client_id,
                ),
            })
            return

        # Already up-to-date — no payload needed.
        if since_seq >= seq:
            await websocket.send_json({
                "kind": "welcome",
                "mode": "live",
                "seq": seq,
            })
            return

        # Within the ring's reach → catch-up.
        batches = room.batches_since_unlocked(since_seq)
        if batches is not None:
            await websocket.send_json({
                "kind": "welcome",
                "mode": "catch-up",
                "seq": seq,
                "batches": batches,
            })
            return

        # Drifted past the buffer floor → fall back to a full snapshot.
        # Keep the root_id scope so a sub-folder client doesn't receive the
        # whole board (matching the first-connect branch above).
        snapshot = await read_snapshot_payload(
            graph_store=graph_store, board_id=board_id, root_id=root_id,
        )
        await websocket.send_json({
            "kind": "welcome",
            "mode": "snapshot",
            "seq": seq,
            "snapshot": snapshot,
            "presence": room.presence_snapshot_unlocked(
                exclude_client_id=client_id,
            ),
        })


async def _handle_message(  # noqa: C901 — flat kind-dispatch reads better than further nesting
    *,
    websocket: WebSocket,
    raw: str,
    graph_store,
    room: Room,
    client: Client,
    board_id: str,
    user_id: str,
) -> None:
    """Dispatch one inbound frame.

    `op` frames go through the sequencer+applier+broadcaster under the
    room lock; everything else still relays verbatim for Phase 1b. The
    presence path will become a structured `peer-presence` in Phase 3.
    """
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        return

    kind = msg.get("kind") if isinstance(msg, dict) else None

    if kind == "op":
        batch = msg.get("batch") or {}
        client_seq = msg.get("client_seq")
        # Read-only clients (viewers) can't mutate. Reject the op
        # outright; nothing applies, nothing broadcasts. The client
        # surfaces this via a toast / read-only banner.
        if client.role not in _EDIT_ROLES:
            try:
                await websocket.send_json({
                    "kind": "op-rejected",
                    "client_seq": client_seq,
                    "reason": "read-only",
                })
            except Exception:
                logger.debug("collab op-rejected send failed", exc_info=True)
            return
        batch = msg.get("batch")
        if not isinstance(batch, dict):
            batch = {}
        ops = batch.get("ops")
        if not isinstance(ops, list):
            await websocket.send_json({
                "kind": "op-rejected",
                "client_seq": client_seq,
                "reason": "malformed batch",
            })
            return
        async with room.lock:
            try:
                await apply_batch(
                    graph_store=graph_store,
                    board_id=board_id,
                    user_id=user_id,
                    ops=ops,
                )
            except Exception as e:
                # A bad op must not kill the socket for the sender. Reject
                # it, leave seq untouched (no gap), and keep the connection.
                logger.exception("collab apply_batch failed board=%s", board_id, exc_info=e)
                try:
                    await websocket.send_json({
                        "kind": "op-rejected",
                        "client_seq": client_seq,
                        "reason": "apply failed",
                    })
                except Exception:
                    logger.debug("collab op-rejected send failed", exc_info=True)
                return
            # Record in the ring so a reconnecting peer can catch up via
            # `since_seq` without a full snapshot rebuild (Phase 1c.2).
            seq = room.next_seq_unlocked()
            room.remember_batch_unlocked(seq, batch)
            peer_op = json.dumps({"kind": "peer-op", "seq": seq, "batch": batch})
            # Send under the lock so peer-op ordering across peers
            # matches the seq order. Head-of-line latency to one peer
            # blocks the room briefly; per-peer outbox queues are a
            # Phase 3 optimization.
            for c in list(room.clients.values()):
                if c is client:
                    try:
                        await c.socket.send_json({
                            "kind": "op-applied",
                            "seq": seq,
                            "client_seq": client_seq,
                        })
                    except Exception:
                        logger.debug("collab op-applied send failed", exc_info=True)
                else:
                    try:
                        await c.socket.send_text(peer_op)
                    except Exception:
                        logger.debug("collab peer-op send failed", exc_info=True)
        return

    # Presence frames: validate, update the per-room registry, then relay.
    # Storing presence server-side means a freshly-joining peer can see
    # existing peers immediately via the welcome handshake instead of
    # waiting for those peers to re-broadcast. Rejecting malformed frames
    # before relay also keeps the protocol surface defensive (size cap +
    # required fields).
    if kind == "presence":
        state = msg.get("state") if isinstance(msg, dict) else None
        app_client_id = msg.get("clientId") if isinstance(msg, dict) else None
        if not _is_valid_presence(app_client_id, state, raw):
            logger.debug(
                "collab presence rejected board=%s client=%s",
                board_id, client.client_id,
            )
            return
        async with room.lock:
            room.update_presence_unlocked(str(app_client_id), state)
        # Remember the peer's app-level clientId so the disconnect-side
        # cleanup can clear the matching registry entry + emit a leave
        # frame keyed correctly. Last-wins if a peer rebrands mid-session
        # (no observed cases, but harmless).
        client.app_client_id = str(app_client_id)
        await room.broadcast(raw, exclude=client)
        return

    if kind == "presence-leave":
        client_id = msg.get("clientId") if isinstance(msg, dict) else None
        if isinstance(client_id, str):
            async with room.lock:
                room.clear_presence_unlocked(client_id)
        await room.broadcast(raw, exclude=client)
        return

    # Other non-op kinds (hello, etc.): relay verbatim.
    await room.broadcast(raw, exclude=client)


def _is_valid_presence(client_id, state, raw: str) -> bool:
    """Reject malformed `presence` frames before they touch the registry.

    Enforces a clientId string, a dict state, and a hard size cap so a
    misbehaving client can't fill `Room.presence` with junk.
    """
    if not isinstance(client_id, str) or not client_id:
        return False
    if not isinstance(state, dict):
        return False
    if len(raw.encode("utf-8")) > MAX_PRESENCE_PAYLOAD_BYTES:
        return False
    return True
