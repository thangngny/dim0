"""Server-side bridge that lets the agent appear as a room client.

The agent runs in-process, not over a WebSocket — but with "collab is
the only edit path" (collab-archi §1) every mutation must produce a
`peer-op` so live browsers see it. The bridge:

1. Performs the actual DB mutation via the existing `GraphStore` API.
2. If a Room exists for that board (someone is connected), broadcasts
   a `peer-op` carrying the corresponding canvas-harness op so peer
   browsers apply the change via `attachSync`'s remote-batch path.

When no Room exists, the broadcast step no-ops; persistence already
happened on step 1. The next browser to open the board will load the
post-mutation snapshot via the welcome handshake.
"""

import json
import logging
import time

from typing import Any

from topix.collab.note_to_wire import (
    link_to_wire_edge,
    note_to_wire_node,
    patch_data_to_wire_patch,
)
from topix.collab.room import RoomRegistry
from topix.datatypes.note.link import Link
from topix.datatypes.note.note import Note
from topix.store.graph import GraphStore

logger = logging.getLogger(__name__)


AGENT_CLIENT_ID = "agent"


class AgentBoardBridge:
    """Mutate the board via GraphStore and broadcast the matching peer-op.

    Public surface mirrors the subset of `GraphStore` that agent tools
    actually call. Each method:
      (a) persists via the underlying GraphStore (with the existing
          per-note locks and embed-skip fast path),
      (b) emits a `peer-op` with the equivalent canvas-harness op tagged
          `is_system: true` so the UI can label the originator.
    """

    def __init__(self, graph_store: GraphStore, registry: RoomRegistry):
        """Wrap the given graph_store + room registry."""
        self._graph_store = graph_store
        self._registry = registry

    async def add_notes(self, *, board_id: str, notes: list[Note]) -> None:
        """Add notes; broadcasts one `peer-op` containing N `node.add` ops."""
        for note in notes:
            if note.graph_uid is None:
                note.graph_uid = board_id
        await self._graph_store.add_notes(nodes=notes)
        ops = [{"type": "node.add", "node": note_to_wire_node(n)} for n in notes]
        await self._broadcast(board_id=board_id, ops=ops)

    async def patch_note(
        self,
        *,
        board_id: str,
        node_id: str,
        data: dict[str, Any],
        user_uid: str | None,
    ) -> Note | None:
        """Patch a note; broadcasts the equivalent `node.update`."""
        result = await self._graph_store.patch_note(
            node_id=node_id, data=data, user_uid=user_uid,
        )
        if result is None:
            return None
        wire_patch = patch_data_to_wire_patch(data)
        if wire_patch:
            await self._broadcast(
                board_id=board_id,
                ops=[{
                    "type": "node.update",
                    "id": node_id,
                    "patch": wire_patch,
                    "prev": {},
                }],
            )
        return result

    async def delete_node(
        self,
        *,
        board_id: str,
        node_id: str,
        user_uid: str | None,
    ) -> None:
        """Delete a note; broadcasts `node.remove`."""
        await self._graph_store.delete_node(node_id=node_id, user_uid=user_uid)
        await self._broadcast(
            board_id=board_id,
            ops=[{"type": "node.remove", "node": {"id": node_id}}],
        )

    async def add_links(self, *, board_id: str, links: list[Link]) -> None:
        """Add links; broadcasts one `peer-op` with N `edge.add` ops."""
        for link in links:
            if link.graph_uid is None:
                link.graph_uid = board_id
        await self._graph_store.add_links(links=links)

        # Resolve source/target node sizes so the wire edge carries
        # `localOffset: {w/2, h/2}` — canvas-harness crashes on an
        # attached endpoint without a localOffset.
        endpoint_ids = {
            uid
            for link in links
            for uid in (link.source, link.target)
            if uid
        }
        node_sizes = await self._resolve_node_sizes(endpoint_ids)

        ops = [
            {"type": "edge.add", "edge": link_to_wire_edge(link, node_sizes=node_sizes)}
            for link in links
        ]
        await self._broadcast(board_id=board_id, ops=ops)

    async def _resolve_node_sizes(
        self, node_ids: set[str],
    ) -> dict[str, tuple[float, float]]:
        """Lookup `(w, h)` for the given node ids from the GraphStore.

        Used to default edge endpoint `localOffset` to node center for
        agent-emitted links (the agent doesn't track endpoint offsets
        itself; the convention is "point at the node's center").
        Missing nodes are silently dropped from the returned map;
        callers fall back to `(0, 0)`.
        """
        if not node_ids:
            return {}
        try:
            notes = await self._graph_store.get_nodes(list(node_ids))
        except Exception:
            logger.exception("agent bridge node-size lookup failed ids=%s", node_ids)
            return {}
        result: dict[str, tuple[float, float]] = {}
        for note in notes:
            size = getattr(note.properties, "node_size", None)
            inner = getattr(size, "size", None) if size is not None else None
            w = getattr(inner, "width", None)
            h = getattr(inner, "height", None)
            if w is not None and h is not None:
                result[note.id] = (float(w), float(h))
        return result

    async def delete_link(self, *, board_id: str, link_id: str) -> None:
        """Delete a link; broadcasts `edge.remove`."""
        await self._graph_store.delete_link(link_id=link_id)
        await self._broadcast(
            board_id=board_id,
            ops=[{"type": "edge.remove", "edge": {"id": link_id}}],
        )

    async def delete_subtree(
        self,
        *,
        board_id: str,
        node_id: str,
        confirm: bool = False,
        user_uid: str | None = None,
    ) -> dict:
        """Delete a node and its descendants plus internal edges.

        Two-phase: `confirm=False` returns a preview of affected counts;
        `confirm=True` performs the delete and broadcasts `node.remove` +
        `edge.remove` ops.
        """
        nodes = await self._graph_store.get_nodes([node_id])
        if not nodes or nodes[0].graph_uid != board_id:
            raise ValueError("Node not found in the current board scope.")

        descendants = await self._graph_store.get_nodes_descendants([node_id])
        subtree_ids = [node_id] + [d.id for d in descendants]

        graph = await self._graph_store.get_graph(board_id)
        edge_ids = [
            e.id for e in (graph.edges if graph else [])
            if e.source in subtree_ids or e.target in subtree_ids
        ]

        if not confirm:
            return {"preview": {"nodes": len(subtree_ids), "edges": len(edge_ids)}}

        await self._graph_store.delete_nodes(node_ids=subtree_ids, user_uid=user_uid)
        if edge_ids:
            await self._graph_store.delete_links(link_ids=edge_ids)

        ops = (
            [{"type": "node.remove", "node": {"id": nid}} for nid in subtree_ids]
            + [{"type": "edge.remove", "edge": {"id": eid}} for eid in edge_ids]
        )
        await self._broadcast(board_id=board_id, ops=ops)
        return {
            "deleted": {"nodes": len(subtree_ids), "edges": len(edge_ids)},
            "node_ids": subtree_ids,
            "edge_ids": edge_ids,
        }

    async def change_note_kind(
        self,
        *,
        board_id: str,
        node_id: str,
        kind: str,
        user_uid: str | None = None,
    ) -> Note | None:
        """Re-style a note to a research kind (shape + color + size).

        Kind is not a stored Note field; the visible kind is the node's
        shape + palette. Patches `style` (shape + canonical colors from
        `build_research_style`) and re-fits `node_size` for the new shape.
        Delegates the persist + `node.update` broadcast to `patch_note`.
        """
        from topix.agents.notes.service import get_default_note_size
        from topix.datatypes.property import SizeProperty
        from topix.integrations.research_meta import merge_research_metadata
        from topix.integrations.research_style import (
            build_research_style,
            get_kind_visual,
        )
        from topix.utils.graph.text_measure import estimate_node_size

        notes = await self._graph_store.get_nodes([node_id])
        if not notes:
            return None
        note = notes[0]
        if note.graph_uid != board_id:
            raise ValueError("Note does not belong to the current board scope.")

        norm = merge_research_metadata(kind, {}).normalized_kind()
        vis = get_kind_visual(norm)
        style = build_research_style(norm)

        width, height = get_default_note_size(vis.shape)
        body = note.content.markdown if note.content else ""
        fitted = estimate_node_size(vis.shape, width, body, style.font_size)
        if fitted is not None:
            width, height = fitted

        data: dict[str, Any] = {
            "style": {
                "type": vis.shape.value,
                "background_color": style.background_color,
                "stroke_color": style.stroke_color,
                "text_color": style.text_color,
                "roundness": style.roundness,
            },
            "properties": {
                "node_size": SizeProperty(
                    size=SizeProperty.Size(width=width, height=height)
                ).model_dump(),
            },
        }
        return await self.patch_note(
            board_id=board_id, node_id=node_id, data=data, user_uid=user_uid,
        )

    async def reparent_note(
        self,
        *,
        board_id: str,
        node_id: str,
        new_parent_id: str | None,
        user_uid: str | None = None,
    ) -> Note | None:
        """Move a note under a new parent (or to the board root when None).

        Rejects cycles: `new_parent_id` must not be `node_id` or one of its
        descendants. Delegates persist + `node.update` (carrying the new
        `parent_id` via `patch_data_to_wire_patch`) to `patch_note`.
        """
        notes = await self._graph_store.get_nodes([node_id])
        if not notes:
            return None
        note = notes[0]
        if note.graph_uid != board_id:
            raise ValueError("Note does not belong to the current board scope.")

        if new_parent_id is not None:
            if new_parent_id == node_id:
                raise ValueError("A node cannot be its own parent.")
            # Cycle check: new_parent must not be node_id or a descendant.
            descendants = await self._graph_store.get_nodes_descendants([node_id])
            descendant_ids = {n.id for n in descendants}
            if new_parent_id in descendant_ids:
                raise ValueError(
                    "Cannot reparent under a descendant — that would create a cycle."
                )
            parent_notes = await self._graph_store.get_nodes([new_parent_id])
            if not parent_notes or parent_notes[0].graph_uid != board_id:
                raise ValueError("New parent does not belong to the current board scope.")

        data: dict[str, Any] = {"parent_id": new_parent_id}
        return await self.patch_note(
            board_id=board_id, node_id=node_id, data=data, user_uid=user_uid,
        )

    async def merge_notes(
        self,
        *,
        board_id: str,
        node_ids: list[str],
        target_id: str,
        confirm: bool = False,
        user_uid: str | None = None,
    ) -> dict:
        """Fold several notes into one target note.

        Appends each non-target note's content to the target, repoints
        edges that referenced a non-target note onto the target, then
        deletes the absorbed notes and any now-duplicate (self-loop)
        edges. Two-phase via `confirm`.
        """
        if not node_ids or target_id not in node_ids:
            raise ValueError("node_ids must be non-empty and include target_id.")
        others = [nid for nid in node_ids if nid != target_id]
        if not others:
            raise ValueError("Nothing to merge — target is the only node given.")

        notes = await self._graph_store.get_nodes(node_ids)
        by_id = {n.id: n for n in notes}
        if target_id not in by_id:
            raise ValueError(f"Target {target_id} not found.")
        for nid in node_ids:
            if nid not in by_id or by_id[nid].graph_uid != board_id:
                raise ValueError(f"Node {nid} not in the current board scope.")

        graph = await self._graph_store.get_graph(board_id)
        edges = graph.edges if graph else []
        # Edges that touch an absorbed node (to repoint or delete).
        affected = [e for e in edges if e.source in others or e.target in others]
        # Edges that would become self-loops on the target after repoint.
        # Mirrors the repoint: an endpoint in `others` becomes `target_id`,
        # an endpoint not in `others` stays itself.
        self_loop_ids = [
            e.id for e in affected
            if (target_id if e.source in others else e.source)
               == (target_id if e.target in others else e.target)
        ]
        repoint = [e for e in affected if e.id not in self_loop_ids]

        if not confirm:
            return {
                "preview": {
                    "absorbed": len(others),
                    "edges_repointed": len(repoint),
                    "edges_dropped": len(self_loop_ids),
                },
            }

        # 1) Append absorbed content into the target.
        target = by_id[target_id]
        base = target.content.markdown if target.content else ""
        appended = "\n\n---\n\n".join(
            [base]
            + [(by_id[nid].content.markdown if by_id[nid].content else "") for nid in others]
        ).strip()
        data: dict[str, Any] = {"content": {"markdown": appended}}
        if target.label and target.label.markdown:
            # Re-stamp the existing label so the deep-merge doesn't drop it.
            data["label"] = {"markdown": target.label.markdown}
        await self.patch_note(
            board_id=board_id, node_id=target_id, data=data, user_uid=user_uid,
        )

        # 2) Repoint edges onto the target; broadcast edge.update per edge.
        update_calls: list[tuple[str, dict]] = []
        ops: list[dict[str, Any]] = []
        for e in repoint:
            new_source = target_id if e.source in others else e.source
            new_target = target_id if e.target in others else e.target
            update_calls.append((e.id, {"source": new_source, "target": new_target}))
            ops.append({
                "type": "edge.update",
                "id": e.id,
                "patch": {
                    "source": {"nodeId": new_source},
                    "target": {"nodeId": new_target},
                },
                "prev": {},
            })
        if update_calls:
            await self._graph_store.update_links(updates=update_calls)

        # 3) Drop edges that would self-loop + delete absorbed notes.
        if self_loop_ids:
            await self._graph_store.delete_links(link_ids=self_loop_ids)
        await self._graph_store.delete_nodes(node_ids=others, user_uid=user_uid)

        ops += [{"type": "edge.remove", "edge": {"id": eid}} for eid in self_loop_ids]
        ops += [{"type": "node.remove", "node": {"id": nid}} for nid in others]
        await self._broadcast(board_id=board_id, ops=ops)
        return {
            "deleted": {"nodes": len(others), "edges": len(self_loop_ids)},
            "repointed": len(repoint),
            "target_id": target_id,
        }

    async def split_note(
        self,
        *,
        board_id: str,
        node_id: str,
        parts: list[str],
        confirm: bool = False,
        delete_original: bool = True,
        user_uid: str | None = None,
    ) -> dict:
        """Split one note into N sibling notes carrying the given content chunks.

        New notes inherit the original's `parent_id`. Inbound edges to the
        original are repointed onto the first new note. The original is
        deleted when `delete_original=True`. Two-phase via `confirm`.
        """
        if not parts:
            raise ValueError("parts must be a non-empty list of content chunks.")

        notes = await self._graph_store.get_nodes([node_id])
        if not notes or notes[0].graph_uid != board_id:
            raise ValueError("Node not found in the current board scope.")
        original = notes[0]

        graph = await self._graph_store.get_graph(board_id)
        inbound = [
            e for e in (graph.edges if graph else [])
            if e.target == node_id and e.source != node_id
        ]

        if not confirm:
            return {
                "preview": {
                    "new_nodes": len(parts),
                    "inbound_edges_repointed": len(inbound),
                    "delete_original": delete_original,
                },
            }

        from topix.agents.notes.service import build_note

        new_ids: list[str] = []
        for chunk in parts:
            child = await build_note(
                graph_store=self._graph_store,
                graph_uid=board_id,
                label=(original.label.markdown if original.label else None),
                content=chunk,
                note_type=original.style.type,
                parent_id=original.parent_id,
            )
            await self.add_notes(board_id=board_id, notes=[child])
            new_ids.append(child.id)

        # Repoint inbound edges onto the first new note.
        if inbound and new_ids:
            updates = [(e.id, {"target": new_ids[0]}) for e in inbound]
            await self._graph_store.update_links(updates=updates)

        ops: list[dict[str, Any]] = []
        if delete_original:
            await self._graph_store.delete_nodes(node_ids=[node_id], user_uid=user_uid)
            ops.append({"type": "node.remove", "node": {"id": node_id}})
        ops += [
            {
                "type": "edge.update",
                "id": e.id,
                "patch": {"target": {"nodeId": new_ids[0]}},
                "prev": {},
            }
            for e in inbound
        ]
        await self._broadcast(board_id=board_id, ops=ops)
        return {"created_ids": new_ids, "delete_original": delete_original}

    # ------------------------------------------------------------------

    async def _broadcast(self, *, board_id: str, ops: list[dict[str, Any]]) -> None:
        """Send a `peer-op` to every connected client in `board_id`'s room.

        Acquires `room.lock` so the assigned seq is consistent with the
        broadcast ordering — same invariant the human WS handler uses.
        No-ops when no Room exists (no live session = no listeners).
        """
        room = self._registry.get(board_id)
        if room is None:
            return
        async with room.lock:
            seq = room.next_seq_unlocked()
            batch = {
                "id": f"agent-{seq}",
                "clientId": AGENT_CLIENT_ID,
                "ts": int(time.time() * 1000),
                "origin": "remote",
                "ops": ops,
                "is_system": True,
            }
            # Record in the ring so a reconnecting peer can catch up on
            # agent-emitted broadcasts without a full snapshot rebuild.
            room.remember_batch_unlocked(seq, batch)
            peer_op = json.dumps({
                "kind": "peer-op",
                "seq": seq,
                "batch": batch,
            })
            for c in list(room.clients.values()):
                try:
                    await c.socket.send_text(peer_op)
                except Exception:
                    logger.debug("agent bridge peer-op send failed", exc_info=True)
