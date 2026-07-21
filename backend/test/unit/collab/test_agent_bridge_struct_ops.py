"""Tests for AgentBoardBridge structural ops (kind/reparent/subtree/merge/split/relayout).

These differ from `test_agent_bridge.py` in that they need a working
GraphStore — `change_note_kind` reads the note back via `get_nodes` and
delegates the persist + broadcast to `patch_note`, so the fake store
must actually deep-merge patches and return the merged note (the brief
asserts on the returned `style.type.value` and `background_color`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from typing import Any

import pytest
import pytest_asyncio

from topix.collab.agent_bridge import AgentBoardBridge
from topix.collab.room import RoomRegistry
from topix.datatypes.note.note import Note
from topix.datatypes.note.style import NodeType


@dataclass
class _FakeGraph:
    """Stand-in for the Pydantic Graph model — exposes `.nodes`."""

    nodes: list[Note] = field(default_factory=list)
    edges: list[Any] = field(default_factory=list)


class _MemGraphStore:
    """Minimal in-memory GraphStore for structural-op bridge tests.

    Supports the subset of GraphStore methods that `build_note`,
    `change_note_kind`, and `patch_note` exercise: `get_graph`,
    `get_nodes`, `add_notes`, and a deep-merging `patch_note` that
    returns the merged `Note` (so tests can assert on the result).
    """

    def __init__(self) -> None:
        """Init."""
        self.notes: dict[str, Note] = {}
        self.links: dict[str, Any] = {}

    async def get_graph(
        self, graph_uid: str, root_id: str | None = None,
    ) -> _FakeGraph:
        """Return notes + edges scoped by graph_uid (and parent_id for links)."""
        scoped_nodes = [
            n for n in self.notes.values()
            if n.graph_uid == graph_uid
            and (
                (root_id is None and n.parent_id is None)
                or (root_id is not None and n.parent_id == root_id)
            )
        ]
        # Mirror GraphStore._link_is_visible_in_scope: when root_id is
        # None only links with parent_id is None are visible; otherwise
        # links whose parent_id matches root_id.
        scoped_edges = [
            link for link in self.links.values()
            if link.graph_uid == graph_uid
            and (
                (root_id is None and link.parent_id is None)
                or (root_id is not None and link.parent_id == root_id)
            )
        ]
        return _FakeGraph(nodes=scoped_nodes, edges=scoped_edges)

    async def get_nodes(self, node_ids: list[str]) -> list[Note]:
        """Return notes by id, preserving input order, skipping missing."""
        return [self.notes[nid] for nid in node_ids if nid in self.notes]

    async def add_notes(self, nodes: list[Note]) -> None:
        """Store notes by id."""
        for note in nodes:
            self.notes[note.id] = note

    async def add_links(self, links: list[Any]) -> None:
        """Store links by id."""
        for link in links:
            self.links[link.id] = link

    async def delete_links(self, link_ids: list[str]) -> None:
        """Remove links by id."""
        for lid in link_ids:
            self.links.pop(lid, None)

    async def delete_nodes(
        self,
        node_ids: list[str],
        hard_delete: bool = True,
        user_uid: str | None = None,
    ) -> None:
        """Remove the given nodes (caller already expanded descendants)."""
        for nid in node_ids:
            self.notes.pop(nid, None)

    async def patch_note(
        self, *, node_id: str, data: dict[str, Any], user_uid: str | None = None,
    ) -> Note | None:
        """Deep-merge `data` into the stored note and return the merged note."""
        existing = self.notes.get(node_id)
        if existing is None:
            return None
        merged = _deep_merge(existing.model_dump(exclude_none=False), data)
        merged["id"] = node_id
        updated = Note.model_validate(merged)
        self.notes[node_id] = updated
        return updated

    async def get_nodes_descendants(self, node_ids: list[str]) -> list[Note]:
        """Multi-root BFS over `parent_id` — roots excluded from result.

        Mirrors `GraphStore.get_nodes_descendants` semantics: visit-once,
        descendants only (roots already known to the caller).
        """
        if not node_ids:
            return []
        seeds = [self.notes[nid] for nid in node_ids if nid in self.notes]
        if not seeds:
            return []
        visited: set[str] = set(node_ids)
        frontier: list[str] = list(node_ids)
        out: list[Note] = []
        while frontier:
            children = [
                n for n in self.notes.values()
                if n.parent_id in frontier and n.id not in visited
            ]
            next_frontier: list[str] = []
            for child in children:
                if child.id in visited:
                    continue
                visited.add(child.id)
                out.append(child)
                next_frontier.append(child.id)
            frontier = next_frontier
        return out


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge `patch` into `base` (mirrors GraphStore._deep_merge_dict)."""
    merged = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


async def _make_note(
    graph_store: _MemGraphStore,
    board_id: str,
    label: str = "Q",
    content: str = "body",
) -> Note:
    """Build and persist a rectangle note via the real `build_note`."""
    from topix.agents.notes.service import build_note
    note = await build_note(
        graph_store=graph_store,
        graph_uid=board_id,
        label=label,
        content=content,
        note_type=NodeType.RECTANGLE,
        parent_id=None,
    )
    await graph_store.add_notes([note])
    return note


@pytest_asyncio.fixture
async def graph_store() -> _MemGraphStore:
    """Provide a fresh in-memory GraphStore per test."""
    return _MemGraphStore()


@pytest.fixture
def board_id() -> str:
    """Provide a stable board id for tests."""
    return "b1"


@pytest.fixture
def room_registry() -> RoomRegistry:
    """Provide a fresh room registry (no live rooms unless a test joins one)."""
    return RoomRegistry()


@pytest_asyncio.fixture
async def agent_bridge(
    graph_store: _MemGraphStore, room_registry: RoomRegistry,
) -> AgentBoardBridge:
    """Provide an AgentBoardBridge wired to the in-memory store + registry."""
    return AgentBoardBridge(graph_store=graph_store, registry=room_registry)


@pytest.mark.asyncio
async def test_change_note_kind_updates_shape_and_colors(
    graph_store: _MemGraphStore,
    board_id: str,
    agent_bridge: AgentBoardBridge,
) -> None:
    """Re-styling a rectangle note to a finding swaps shape + palette.

    finding -> SOFT_DIAMOND shape, emerald family. The returned note
    carries the new style (persisted by `patch_note`'s deep-merge).
    """
    note = await _make_note(graph_store, board_id, label="Why?", content="x")
    updated = await agent_bridge.change_note_kind(
        board_id=board_id, node_id=note.id, kind="finding",
    )
    assert updated is not None
    # finding -> SOFT_DIAMOND shape, emerald family
    assert updated.style.type.value == "soft-diamond"
    assert updated.style.background_color  # non-default color set


@pytest.mark.asyncio
async def test_reparent_note_moves_parent(graph_store, board_id, agent_bridge):
    parent = await _make_note(graph_store, board_id, label="P")
    child = await _make_note(graph_store, board_id, label="C")
    updated = await agent_bridge.reparent_note(
        board_id=board_id, node_id=child.id, new_parent_id=parent.id)
    assert updated.parent_id == parent.id


@pytest.mark.asyncio
async def test_reparent_note_rejects_cycle(graph_store, board_id, agent_bridge):
    parent = await _make_note(graph_store, board_id, label="P")
    child = await _make_note(graph_store, board_id, label="C")
    await agent_bridge.reparent_note(
        board_id=board_id, node_id=child.id, new_parent_id=parent.id)
    # moving parent under child would create a cycle
    with pytest.raises(ValueError):
        await agent_bridge.reparent_note(
            board_id=board_id, node_id=parent.id, new_parent_id=child.id)


@pytest.mark.asyncio
async def test_delete_subtree_preview_then_confirm(graph_store, board_id, agent_bridge):
    """Two-phase delete: preview reports real counts, confirm removes subtree."""
    from topix.datatypes.note.link import Link
    root = await _make_note(graph_store, board_id, label="R")
    child = await _make_note(graph_store, board_id, label="C")
    await agent_bridge.reparent_note(board_id=board_id, node_id=child.id, new_parent_id=root.id)
    link = Link(source=root.id, target=child.id, graph_uid=board_id)
    await agent_bridge.add_links(board_id=board_id, links=[link])

    preview = await agent_bridge.delete_subtree(board_id=board_id, node_id=root.id, confirm=False)
    assert preview["preview"]["nodes"] >= 2  # root + child
    assert preview["preview"]["edges"] >= 1

    result = await agent_bridge.delete_subtree(board_id=board_id, node_id=root.id, confirm=True)
    assert result["deleted"]["nodes"] >= 2
    remaining = await graph_store.get_nodes([root.id, child.id])
    assert remaining == []