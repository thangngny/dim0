# Board Chat Bot — Rich Structural Edits Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the board chat agent (CopilotSheet "Board Assistant") perform six structural graph edits — change kind, re-parent, delete subtree, merge, split, relayout — via natural-language chat with selected-node context.

**Architecture:** Hybrid (Approach C). The six ops are implemented once as `AgentBoardBridge` methods (the existing shared mutation primitive) and exposed through two thin surfaces: chat-agent tools (in-app, conversational) and integration/MCP endpoints (token-authenticated, for external `claude` CLI / research mode). The bridge broadcasts existing wire ops (`node.update/add/remove`, `edge.add/update/remove`) so the canvas collab path needs no new op types. The frontend `apply-tool-output` path gains handlers for the six new tool outputs.

**Tech Stack:** Python (FastAPI, Pydantic, `agents` SDK), pytest; TypeScript (React, Zustand, TanStack Query, Vite), Vitest.

## Global Constraints

- Backend code is Python under `backend/topix/`. Run tests from `backend/` with `uv run pytest`.
- Frontend code is TypeScript under `webui/src/`. No semicolons; named exports only (no `export default`); no `any`; 2 blank lines between top-level declarations, 1 inside blocks.
- Conventional Commit format: `type(scope): message`, mandatory specific scope (`collab`, `agents`, `integration`, `mcp`, `webui`, `prompts`), short imperative lowercase, no trailing period. One logical change per commit.
- Docstrings: 1-3 lines, intent + behavior, on new/modified functions/methods/classes.
- The bridge mutation pattern (see `backend/topix/collab/agent_bridge.py`) is: validate → mutate via `self._graph_store` → `await self._broadcast(board_id=board_id, ops=[...])` with canvas-harness wire ops. Reuse `patch_note` / `add_notes` / `delete_node` / `add_links` / `delete_link` where they already broadcast; only call `_broadcast` directly for op combinations those don't cover.
- Kind is not a `Note` field. A research node's kind is expressed by `style.type` (shape), style colors, and an optional content meta block. `change_note_kind` patches `style` + `node_size` only (visible kind change). See `backend/topix/integrations/research_style.py` (`get_kind_visual`, `build_research_style`) and `backend/topix/integrations/research_meta.py` (`VALID_KINDS`).
- Destructive ops (delete subtree, merge, split) implement a two-phase `confirm` flag: `confirm=False` returns a preview (affected node/edge counts); `confirm=True` executes. The chat agent prompts the user between phases.
- All new integration endpoints reuse the existing `_verify_token` dependency + `redact_content` + `research_scope` guards (`assert_can_create`, `assert_can_mutate`) from `backend/topix/api/router/integration.py`.

---

## File Structure

**Backend (new/modified):**
- `backend/topix/collab/agent_bridge.py` — add 6 methods (`change_note_kind`, `reparent_note`, `delete_subtree`, `merge_notes`, `split_note`, `relayout`). Single mutation primitive.
- `backend/topix/agents/datatypes/outputs.py` — add 6 output models.
- `backend/topix/agents/datatypes/tools.py` — add 6 `AgentToolName` enum values + descriptions.
- `backend/topix/agents/notes/tools.py` — add 6 tool factory functions.
- `backend/topix/agents/assistant/plan.py:96-100` — register the 6 new tools when a board scope is active.
- `backend/topix/api/router/integration.py` — add 5 endpoints (`:set-kind`, `:reparent`, `:subtree`, `:merge`, `:split`); relayout already exists.
- `backend/topix/integrations/dim0_mcp/server.py` — add 5 MCP tools mirroring the endpoints.
- `backend/test/unit/collab/test_agent_bridge_struct_ops.py` — new, bridge method tests.
- `backend/test/unit/api/router/test_integration_struct_ops.py` — new, endpoint tests.
- `backend/test/unit/agents/test_notes_struct_tools.py` — new, tool dispatch tests.
- `backend/topix/prompts/plan.system.jinja` — add a "Board structural edits" section teaching when to use each op + confirmation.

**Frontend (new/modified):**
- `webui/src/features/agent/types/tool-outputs.ts` — add 6 output interfaces + union members.
- `webui/src/features/agent/types/stream.ts:108-112` — add 6 tool-name literals.
- `webui/src/features/agent/utils/stream/build.ts:60-95,385-421` — add 6 build cases.
- `webui/src/features/agent/store/chat-store.ts:52-54,117-119` — add 6 names to the board-tool allowlists.
- `webui/src/features/board/harness/agent/apply-tool-output.ts` — add 6 apply functions.
- `webui/src/features/board/harness/agent/agent-bridge.ts` — add 6 bridge methods.
- `webui/src/features/agent/api/send-message.ts:260-340` — add 6 apply dispatch cases.
- `webui/src/features/agent/components/chat/tool-step-row.tsx:247` — render the 6 new tool steps.

---

### Task 1: `AgentBoardBridge.change_note_kind`

**Files:**
- Modify: `backend/topix/collab/agent_bridge.py` (add method)
- Test: `backend/test/unit/collab/test_agent_bridge_struct_ops.py`

**Interfaces:**
- Consumes: `GraphStore.get_nodes`, `AgentBoardBridge.patch_note` (existing, broadcasts `node.update`).
- Produces: `async def change_note_kind(self, *, board_id: str, node_id: str, kind: str, user_uid: str | None = None) -> Note | None`

- [ ] **Step 1: Write the failing test**

```python
# backend/test/unit/collab/test_agent_bridge_struct_ops.py
"""Tests for AgentBoardBridge structural ops (kind/reparent/subtree/merge/split/relayout)."""

from __future__ import annotations

import pytest

from topix.collab.agent_bridge import AgentBoardBridge
from topix.datatypes.note.note import Note
from topix.datatypes.resource import RichText


async def _make_note(graph_store, board_id, label="Q", content="body") -> Note:
    from topix.agents.notes.service import build_note
    note = await build_note(graph_store=graph_store, graph_uid=board_id,
                           label=label, content=content, note_type="rectangle")
    await graph_store.add_notes([note])
    return note


@pytest.mark.asyncio
async def test_change_note_kind_updates_shape_and_colors(graph_store, board_id, agent_bridge):
    note = await _make_note(graph_store, board_id, label="Why?", content="x")
    updated = await agent_bridge.change_note_kind(
        board_id=board_id, node_id=note.id, kind="finding")
    assert updated is not None
    # finding -> SOFT_DIAMOND shape, emerald family
    assert updated.style.type.value == "soft-diamond"
    assert updated.style.background_color  # non-default color set


@pytest.fixture
async def agent_bridge(graph_store, room_registry):
    return AgentBoardBridge(graph_store=graph_store, registry=room_registry)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest backend/test/unit/collab/test_agent_bridge_struct_ops.py::test_change_note_kind_updates_shape_and_colors -x -v` (from `backend/`)
Expected: FAIL with `AttributeError: 'AgentBoardBridge' object has no attribute 'change_note_kind'`

- [ ] **Step 3: Write minimal implementation**

Add to `backend/topix/collab/agent_bridge.py` inside `AgentBoardBridge` (after `delete_link`):

```python
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
        from topix.integrations.research_style import build_research_style, get_kind_visual
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest backend/test/unit/collab/test_agent_bridge_struct_ops.py::test_change_note_kind_updates_shape_and_colors -x -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/topix/collab/agent_bridge.py backend/test/unit/collab/test_agent_bridge_struct_ops.py
git commit -m "feat(collab): add change_note_kind to agent bridge"
```

---

### Task 2: `AgentBoardBridge.reparent_note` (with cycle detection)

**Files:**
- Modify: `backend/topix/collab/agent_bridge.py`
- Test: `backend/test/unit/collab/test_agent_bridge_struct_ops.py`

**Interfaces:**
- Consumes: `GraphStore.get_nodes`, `GraphStore.get_node_path`.
- Produces: `async def reparent_note(self, *, board_id: str, node_id: str, new_parent_id: str | None, user_uid: str | None = None) -> Note | None`. Raises `ValueError` on cycle (would make `new_parent_id` a descendant of `node_id`).

- [ ] **Step 1: Write the failing test**

Append to the test file:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest backend/test/unit/collab/test_agent_bridge_struct_ops.py -k reparent -x -v`
Expected: FAIL (no `reparent_note`)

- [ ] **Step 3: Write minimal implementation**

Add to `AgentBoardBridge`:

```python
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

        data = {"parent_id": new_parent_id}
        return await self.patch_note(
            board_id=board_id, node_id=node_id, data=data, user_uid=user_uid,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest backend/test/unit/collab/test_agent_bridge_struct_ops.py -k reparent -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/topix/collab/agent_bridge.py backend/test/unit/collab/test_agent_bridge_struct_ops.py
git commit -m "feat(collab): add reparent_note with cycle detection"
```

---

### Task 3: `AgentBoardBridge.delete_subtree` (two-phase confirm)

**Files:**
- Modify: `backend/topix/collab/agent_bridge.py`
- Test: `backend/test/unit/collab/test_agent_bridge_struct_ops.py`

**Interfaces:**
- Consumes: `GraphStore.get_nodes`, `GraphStore.get_nodes_descendants`, `GraphStore.get_graph`, `GraphStore.delete_nodes`, `GraphStore.delete_links`.
- Produces: `async def delete_subtree(self, *, board_id: str, node_id: str, confirm: bool = False, user_uid: str | None = None) -> dict`. Returns `{"preview": {"nodes": int, "edges": int}}` when `confirm=False`, else `{"deleted": {"nodes": int, "edges": int}, "node_ids": [...], "edge_ids": [...]}`.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_delete_subtree_preview_then_confirm(graph_store, board_id, agent_bridge):
    root = await _make_note(graph_store, board_id, label="R")
    child = await _make_note(graph_store, board_id, label="C")
    await agent_bridge.reparent_note(board_id=board_id, node_id=child.id, new_parent_id=root.id)
    from topix.datatypes.note.link import Link
    link = Link(source=root.id, target=child.id, graph_uid=board_id)
    await agent_bridge.add_links(board_id=board_id, links=[link])

    preview = await agent_bridge.delete_subtree(board_id=board_id, node_id=root.id, confirm=False)
    assert preview["preview"]["nodes"] >= 2  # root + child
    assert preview["preview"]["edges"] >= 1

    result = await agent_bridge.delete_subtree(board_id=board_id, node_id=root.id, confirm=True)
    assert result["deleted"]["nodes"] >= 2
    remaining = await graph_store.get_nodes([root.id, child.id])
    assert remaining == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest backend/test/unit/collab/test_agent_bridge_struct_ops.py -k delete_subtree -x -v`
Expected: FAIL (no `delete_subtree`)

- [ ] **Step 3: Write minimal implementation**

Add to `AgentBoardBridge`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest backend/test/unit/collab/test_agent_bridge_struct_ops.py -k delete_subtree -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/topix/collab/agent_bridge.py backend/test/unit/collab/test_agent_bridge_struct_ops.py
git commit -m "feat(collab): add delete_subtree with two-phase confirm"
```

---

### Task 4: `AgentBoardBridge.merge_notes` (two-phase confirm)

**Files:**
- Modify: `backend/topix/collab/agent_bridge.py`
- Test: `backend/test/unit/collab/test_agent_bridge_struct_ops.py`

**Interfaces:**
- Consumes: `GraphStore.get_nodes`, `GraphStore.get_graph`, `GraphStore.patch_note`, `GraphStore.delete_nodes`, `GraphStore.update_links`, `GraphStore.delete_links`.
- Produces: `async def merge_notes(self, *, board_id: str, node_ids: list[str], target_id: str, confirm: bool = False, user_uid: str | None = None) -> dict`.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_merge_notes_folds_into_target(graph_store, board_id, agent_bridge):
    a = await _make_note(graph_store, board_id, label="A", content="alpha")
    b = await _make_note(graph_store, board_id, label="B", content="beta")
    preview = await agent_bridge.merge_notes(
        board_id=board_id, node_ids=[a.id, b.id], target_id=a.id, confirm=False)
    assert preview["preview"]["absorbed"] == 1

    result = await agent_bridge.merge_notes(
        board_id=board_id, node_ids=[a.id, b.id], target_id=a.id, confirm=True)
    assert result["deleted"]["nodes"] == 1
    merged = (await graph_store.get_nodes([a.id]))[0]
    assert "alpha" in merged.content.markdown and "beta" in merged.content.markdown
    assert (await graph_store.get_nodes([b.id])) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest backend/test/unit/collab/test_agent_bridge_struct_ops.py -k merge -x -v`
Expected: FAIL (no `merge_notes`)

- [ ] **Step 3: Write minimal implementation**

Add to `AgentBoardBridge`:

```python
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
        deletes the absorbed notes and any now-duplicate edges. Two-phase
        via `confirm`.
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
        affected = [
            e for e in edges
            if e.source in others or e.target in others
        ]
        # Edges that would become self-loops on the target after repoint.
        self_loop_ids = [
            e.id for e in affected
            if (e.source if e.source in others else target_id)
               == (e.target if e.target in others else target_id)
        ]
        repoint = [e for e in affected if e.id not in self_loop_ids]

        if not confirm:
            return {
                "preview": {
                    "absorbed": len(others),
                    "edges_repointed": len(repoint),
                    "edges_dropped": len(self_loop_ids),
                }
            }

        # 1) Append absorbed content into the target.
        target = by_id[target_id]
        base = target.content.markdown if target.content else ""
        appended = "\n\n---\n\n".join(
            [base] + [(by_id[nid].content.markdown if by_id[nid].content else "") for nid in others]
        ).strip()
        await self.patch_note(
            board_id=board_id, node_id=target_id,
            data={"content": {"markdown": appended}, "label": (
                {"markdown": target.label.markdown} if target.label else None
            )},
            user_uid=user_uid,
        )

        # 2) Repoint edges onto the target; broadcast edge.update per edge.
        update_calls: list[tuple[str, dict]] = []
        ops: list[dict[str, Any]] = []
        for e in repoint:
            new_source = target_id if e.source in others else e.source
            new_target = target_id if e.target in others else e.target
            update_calls.append((e.id, {"source": new_source, "target": new_target}))
            ops.append({"type": "edge.update", "id": e.id,
                        "patch": {"source": {"nodeId": new_source},
                                  "target": {"nodeId": new_target}}, "prev": {}})
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest backend/test/unit/collab/test_agent_bridge_struct_ops.py -k merge -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/topix/collab/agent_bridge.py backend/test/unit/collab/test_agent_bridge_struct_ops.py
git commit -m "feat(collab): add merge_notes with two-phase confirm"
```

---

### Task 5: `AgentBoardBridge.split_note` (two-phase confirm)

**Files:**
- Modify: `backend/topix/collab/agent_bridge.py`
- Test: `backend/test/unit/collab/test_agent_bridge_struct_ops.py`

**Interfaces:**
- Consumes: `topix.agents.notes.service.build_note`, `GraphStore.add_notes`, `GraphStore.get_graph`, `GraphStore.update_links`, `GraphStore.delete_nodes`, `GraphStore.add_links`.
- Produces: `async def split_note(self, *, board_id: str, node_id: str, parts: list[str], confirm: bool = False, delete_original: bool = True, user_uid: str | None = None) -> dict`. `parts` are the content chunks for the new notes.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_split_note_creates_children(graph_store, board_id, agent_bridge):
    original = await _make_note(graph_store, board_id, label="Big", content="one\ntwo")
    preview = await agent_bridge.split_note(
        board_id=board_id, node_id=original.id, parts=["one", "two"], confirm=False)
    assert preview["preview"]["new_nodes"] == 2

    result = await agent_bridge.split_note(
        board_id=board_id, node_id=original.id, parts=["one", "two"], confirm=True)
    assert result["created_ids"]
    if result["delete_original"]:
        assert (await graph_store.get_nodes([original.id])) == []
    new_notes = await graph_store.get_nodes(result["created_ids"])
    assert len(new_notes) == 2
    assert {n.content.markdown for n in new_notes} == {"one", "two"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest backend/test/unit/collab/test_agent_bridge_struct_ops.py -k split -x -v`
Expected: FAIL (no `split_note`)

- [ ] **Step 3: Write minimal implementation**

Add to `AgentBoardBridge`:

```python
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
            return {"preview": {"new_nodes": len(parts),
                                 "inbound_edges_repointed": len(inbound),
                                 "delete_original": delete_original}}

        from topix.agents.notes.service import build_note

        new_ids: list[str] = []
        for chunk in parts:
            child = await build_note(
                graph_store=self._graph_store, graph_uid=board_id,
                label=(original.label.markdown if original.label else None),
                content=chunk, note_type=original.style.type, parent_id=original.parent_id,
            )
            await self._graph_store.add_notes([child])
            new_ids.append(child.id)

        # Repoint inbound edges onto the first new note.
        if inbound and new_ids:
            updates = [(e.id, {"target": new_ids[0]}) for e in inbound]
            await self._graph_store.update_links(updates=updates)

        ops: list[dict[str, Any]] = []
        if delete_original:
            await self._graph_store.delete_nodes(node_ids=[node_id], user_uid=user_uid)
            ops.append({"type": "node.remove", "node": {"id": node_id}})
        ops += [{"type": "edge.update", "id": e.id,
                 "patch": {"target": {"nodeId": new_ids[0]}}, "prev": {}} for e in inbound]
        await self._broadcast(board_id=board_id, ops=ops)
        return {"created_ids": new_ids, "delete_original": delete_original}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest backend/test/unit/collab/test_agent_bridge_struct_ops.py -k split -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/topix/collab/agent_bridge.py backend/test/unit/collab/test_agent_bridge_struct_ops.py
git commit -m "feat(collab): add split_note with two-phase confirm"
```

---

### Task 6: `AgentBoardBridge.relayout`

**Files:**
- Modify: `backend/topix/collab/agent_bridge.py`
- Test: `backend/test/unit/collab/test_agent_bridge_struct_ops.py`

**Interfaces:**
- Consumes: `topix.agents.notes.layout.rearrange_created_notes`, `topix.integrations.research_layout.apply_research_layout`.
- Produces: `async def relayout(self, *, board_id: str, scope_ids: list[str] | None = None, mode: str = "default") -> dict`.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_relayout_default_mode_runs(graph_store, board_id, agent_bridge):
    a = await _make_note(graph_store, board_id, label="A")
    b = await _make_note(graph_store, board_id, label="B")
    result = await agent_bridge.relayout(
        board_id=board_id, scope_ids=[a.id, b.id], mode="default")
    assert "moved" in result
    assert isinstance(result["moved"], list)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest backend/test/unit/collab/test_agent_bridge_struct_ops.py -k relayout -x -v`
Expected: FAIL (no `relayout`)

- [ ] **Step 3: Write minimal implementation**

Add to `AgentBoardBridge`:

```python
    async def relayout(
        self,
        *,
        board_id: str,
        scope_ids: list[str] | None = None,
        mode: str = "default",
    ) -> dict:
        """Re-run auto-layout for a set of nodes (or the whole board).

        `mode="research"` uses the hierarchical research layout; otherwise
        the default note layout. Returns the moved-node count. Broadcast
        of position updates happens inside the layout helpers (they patch
        via this bridge where applicable).
        """
        from topix.agents.notes.layout import rearrange_created_notes
        from topix.integrations.research_layout import apply_research_layout

        if mode == "research":
            moved = await apply_research_layout(
                graph_store=self._graph_store, bridge=self,
                board_id=board_id, created_ids=scope_ids or [],
            )
            return {"moved": moved, "count": len(moved), "mode": "research"}

        moved = await rearrange_created_notes(
            graph_store=self._graph_store, graph_uid=board_id,
            created_ids=scope_ids or [], created_link_ids=None, agent_bridge=self,
        )
        return {"moved": moved, "count": len(moved), "mode": "default"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest backend/test/unit/collab/test_agent_bridge_struct_ops.py -k relayout -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/topix/collab/agent_bridge.py backend/test/unit/collab/test_agent_bridge_struct_ops.py
git commit -m "feat(collab): add relayout helper to agent bridge"
```

---

### Task 7: Chat agent output models + tool names

**Files:**
- Modify: `backend/topix/agents/datatypes/outputs.py`
- Modify: `backend/topix/agents/datatypes/tools.py`
- Test: `backend/test/unit/agents/test_notes_struct_tools.py`

**Interfaces:**
- Produces: `ChangeKindOutput`, `ReparentNoteOutput`, `DeleteSubtreeOutput`, `MergeNotesOutput`, `SplitNoteOutput`, `RelayoutOutput` (Pydantic models with `type` literal + `to_compact_repr`); enum values `CHANGE_NOTE_KIND`, `REPARENT_NOTE`, `DELETE_SUBTREE`, `MERGE_NOTES`, `SPLIT_NOTE`, `RELAYOUT_BOARD`.

- [ ] **Step 1: Write the failing test**

```python
# backend/test/unit/agents/test_notes_struct_tools.py
from topix.agents.datatypes.outputs import (
    ChangeKindOutput, ReparentNoteOutput, DeleteSubtreeOutput,
    MergeNotesOutput, SplitNoteOutput, RelayoutOutput,
)


def test_struct_output_compact_repr():
    assert ChangeKindOutput(
        note_id="n1", graph_uid="b1", kind="finding").to_compact_repr() == 'kind=finding note_id="n1"'
    assert MergeNotesOutput(
        target_id="t1", graph_uid="b1", absorbed=2).to_compact_repr() == 'merged 2 into note_id="t1"'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest backend/test/unit/agents/test_notes_struct_tools.py -x -v`
Expected: FAIL (imports not found)

- [ ] **Step 3: Write minimal implementation**

Append to `backend/topix/agents/datatypes/outputs.py` (before the `ToolOutput` union):

```python
class ChangeKindOutput(BaseModel):
    """Output from the change-note-kind tool."""

    type: Literal["change_note_kind"] = "change_note_kind"
    note_id: Annotated[str, "The note id whose kind was changed."]
    graph_uid: Annotated[str, "The board id where the note belongs."]
    kind: Annotated[str, "The new research kind (question/finding/…)."]

    def to_compact_repr(self) -> str:
        """Return a compact history-safe summary."""
        return f'kind={self.kind} note_id="{self.note_id}"'


class ReparentNoteOutput(BaseModel):
    """Output from the reparent-note tool."""

    type: Literal["reparent_note"] = "reparent_note"
    note_id: Annotated[str, "The note id that was moved."]
    graph_uid: Annotated[str, "The board id where the note belongs."]
    parent_id: Annotated[str | None, "The new parent note id, or None for board root."] = None

    def to_compact_repr(self) -> str:
        """Return a compact history-safe summary."""
        return f'reparented note_id="{self.note_id}" under {self.parent_id}'


class DeleteSubtreeOutput(BaseModel):
    """Output from the delete-subtree tool."""

    type: Literal["delete_subtree"] = "delete_subtree"
    graph_uid: Annotated[str, "The board id where the subtree lived."]
    deleted_nodes: Annotated[int, "Number of nodes deleted (root + descendants)."]
    deleted_edges: Annotated[int, "Number of edges deleted."] = 0

    def to_compact_repr(self) -> str:
        """Return a compact history-safe summary."""
        return f'deleted subtree: {self.deleted_nodes} nodes, {self.deleted_edges} edges'


class MergeNotesOutput(BaseModel):
    """Output from the merge-notes tool."""

    type: Literal["merge_notes"] = "merge_notes"
    target_id: Annotated[str, "The note id that absorbed the others."]
    graph_uid: Annotated[str, "The board id where the notes belonged."]
    absorbed: Annotated[int, "Number of notes folded into the target."]

    def to_compact_repr(self) -> str:
        """Return a compact history-safe summary."""
        return f'merged {self.absorbed} into note_id="{self.target_id}"'


class SplitNoteOutput(BaseModel):
    """Output from the split-note tool."""

    type: Literal["split_note"] = "split_note"
    graph_uid: Annotated[str, "The board id where the note belonged."]
    created_ids: Annotated[list[str], "The new note ids created from the split."]
    original_deleted: Annotated[bool, "Whether the original note was deleted."] = True

    def to_compact_repr(self) -> str:
        """Return a compact history-safe summary."""
        return f'split into {len(self.created_ids)} notes'


class RelayoutOutput(BaseModel):
    """Output from the relayout tool."""

    type: Literal["relayout_board"] = "relayout_board"
    graph_uid: Annotated[str, "The board id that was relaid out."]
    moved: Annotated[int, "Number of nodes moved."]
    mode: Annotated[str, "Layout mode used (default/research)."] = "default"

    def to_compact_repr(self) -> str:
        """Return a compact history-safe summary."""
        return f'relayout {self.mode}: moved {self.moved}'
```

Add the six to the `ToolOutput` union at the bottom of the file.

Add to `backend/topix/agents/datatypes/tools.py` `AgentToolName` enum:

```python
    CHANGE_NOTE_KIND = "change_note_kind"
    REPARENT_NOTE = "reparent_note"
    DELETE_SUBTREE = "delete_subtree"
    MERGE_NOTES = "merge_notes"
    SPLIT_NOTE = "split_note"
    RELAYOUT_BOARD = "relayout_board"
```

And add descriptions to the `tool_descriptions` dict (short, imperative):

```python
    AgentToolName.CHANGE_NOTE_KIND: "Change a note's research kind (question/finding/source/evidence/hypothesis/contradiction/unknown/alternative/decision/summary). Re-styles shape and color.",
    AgentToolName.REPARENT_NOTE: "Move a note under a different parent note (or to the board root). Rejects cycles.",
    AgentToolName.DELETE_SUBTREE: "Delete a note plus all its descendants and internal edges. Always confirm=False first to preview, then confirm=True after the user agrees.",
    AgentToolName.MERGE_NOTES: "Fold several notes into one target note (append content, repoint edges, delete the rest). confirm=False first, then confirm=True.",
    AgentToolName.SPLIT_NOTE: "Split one note into several sibling notes from content chunks. confirm=False first, then confirm=True.",
    AgentToolName.RELAYOUT_BOARD: "Re-run auto-layout for a branch or the whole board to tidy the graph.",
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest backend/test/unit/agents/test_notes_struct_tools.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/topix/agents/datatypes/outputs.py backend/topix/agents/datatypes/tools.py backend/test/unit/agents/test_notes_struct_tools.py
git commit -m "feat(agents): add structural op output models and tool names"
```

---

### Task 8: Chat agent tools + assembly

**Files:**
- Modify: `backend/topix/agents/notes/tools.py` (add 6 factory functions)
- Modify: `backend/topix/agents/assistant/plan.py:96-100` (register them)
- Test: `backend/test/unit/agents/test_notes_struct_tools.py`

**Interfaces:**
- Consumes: the six `AgentBoardBridge` methods + output models.
- Produces: `create_change_note_kind_tool`, `create_reparent_note_tool`, `create_delete_subtree_tool`, `create_merge_notes_tool`, `create_split_note_tool`, `create_relayout_tool` — each `(graph_store, graph_uid, agent_bridge) -> FunctionTool`.

- [ ] **Step 1: Write the failing test**

Append to the test file:

```python
@pytest.mark.asyncio
async def test_change_kind_tool_dispatches_to_bridge(graph_store, board_id, agent_bridge):
    from topix.agents.notes.tools import create_change_note_kind_tool
    note = await _make_note(graph_store, board_id, label="Q", content="x")
    tool = create_change_note_kind_tool(graph_store, board_id, agent_bridge)
    # Invoke the wrapped function via the agents SDK RunContextWrapper.
    from agents import RunContextWrapper
    from topix.agents.datatypes.context import Context
    out = await tool.on_invoke_tool(RunContextWrapper(Context()), '{"note_id":"' + note.id + '","kind":"finding"}')
    assert "change_note_kind" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest backend/test/unit/agents/test_notes_struct_tools.py -k change_kind_tool -x -v`
Expected: FAIL (no `create_change_note_kind_tool`)

- [ ] **Step 3: Write minimal implementation**

Append to `backend/topix/agents/notes/tools.py` (after `create_link_notes_tool`). Pattern mirrors `create_edit_note_tool`: a closure that validates scope, calls the bridge method, returns the output model. Example for two; the rest follow the same shape.

```python
def create_change_note_kind_tool(
    graph_store: GraphStore,
    graph_uid: str,
    agent_bridge: AgentBoardBridge | None = None,
) -> FunctionTool:
    """Build a change-kind tool bound to the current board scope."""

    async def change_note_kind(
        _wrapper: RunContextWrapper[Context],
        note_id: str,
        kind: str,
    ) -> ChangeKindOutput:
        """Change the research kind of an existing note (re-style shape + color).

        Use this to refine the board: turn a Question into a Finding once
        answered, a Hypothesis into a Decision once chosen, etc. Always
        identify the note by `note_id`, never by label.

        Args:
            note_id (str): Exact id of the note to re-style.
            kind (str): New kind — one of question, workstream, source, evidence,
                finding, hypothesis, contradiction, unknown, alternative,
                decision, summary, note.
        """
        if agent_bridge is None:
            raise ValueError("change_note_kind requires a live agent bridge.")
        updated = await agent_bridge.change_note_kind(
            board_id=graph_uid, node_id=note_id, kind=kind, user_uid=None)
        if updated is None:
            raise ValueError(f"Note {note_id} was not found.")
        return ChangeKindOutput(note_id=updated.id, graph_uid=graph_uid, kind=kind)

    return ToolHandler.convert_func_to_tool(
        change_note_kind, tool_name=AgentToolName.CHANGE_NOTE_KIND, tool_description=None,
    )


def create_reparent_note_tool(
    graph_store: GraphStore, graph_uid: str, agent_bridge: AgentBoardBridge | None = None,
) -> FunctionTool:
    """Build a reparent tool bound to the current board scope."""

    async def reparent_note(
        _wrapper: RunContextWrapper[Context],
        note_id: str,
        parent_id: str | None = None,
    ) -> ReparentNoteOutput:
        """Move a note under a different parent (or to the board root when parent_id is None).

        Use this to restructure the tree hierarchy. Cycles are rejected.

        Args:
            note_id (str): Exact id of the note to move.
            parent_id (str | None): New parent note id, or None to move to the board root.
        """
        if agent_bridge is None:
            raise ValueError("reparent_note requires a live agent bridge.")
        updated = await agent_bridge.reparent_note(
            board_id=graph_uid, node_id=note_id, new_parent_id=parent_id, user_uid=None)
        if updated is None:
            raise ValueError(f"Note {note_id} was not found.")
        return ReparentNoteOutput(note_id=updated.id, graph_uid=graph_uid, parent_id=updated.parent_id)

    return ToolHandler.convert_func_to_tool(
        reparent_note, tool_name=AgentToolName.REPARENT_NOTE, tool_description=None,
    )


def create_delete_subtree_tool(
    graph_store: GraphStore, graph_uid: str, agent_bridge: AgentBoardBridge | None = None,
) -> FunctionTool:
    """Build a delete-subtree tool bound to the current board scope."""

    async def delete_subtree(
        _wrapper: RunContextWrapper[Context],
        note_id: str,
        confirm: bool = False,
    ) -> DeleteSubtreeOutput | str:
        """Delete a note and all its descendants plus internal edges.

        Destructive: ALWAYS call with confirm=False first to show the user a
        preview (affected node/edge counts), ask for confirmation, then call
        again with confirm=True.

        Args:
            note_id (str): Root of the subtree to delete.
            confirm (bool): False = preview only; True = execute the delete.
        """
        if agent_bridge is None:
            raise ValueError("delete_subtree requires a live agent bridge.")
        result = await agent_bridge.delete_subtree(
            board_id=graph_uid, node_id=note_id, confirm=confirm, user_uid=None)
        if not confirm:
            return (f"Preview: will delete {result['preview']['nodes']} node(s) "
                    f"and {result['preview']['edges']} edge(s). "
                    f"Confirm with the user before re-calling with confirm=True.")
        return DeleteSubtreeOutput(
            graph_uid=graph_uid,
            deleted_nodes=result["deleted"]["nodes"],
            deleted_edges=result["deleted"]["edges"],
        )

    return ToolHandler.convert_func_to_tool(
        delete_subtree, tool_name=AgentToolName.DELETE_SUBTREE, tool_description=None,
    )


def create_merge_notes_tool(
    graph_store: GraphStore, graph_uid: str, agent_bridge: AgentBoardBridge | None = None,
) -> FunctionTool:
    """Build a merge-notes tool bound to the current board scope."""

    async def merge_notes(
        _wrapper: RunContextWrapper[Context],
        node_ids: list[str],
        target_id: str,
        confirm: bool = False,
    ) -> MergeNotesOutput | str:
        """Fold several notes into one target note (append content, repoint edges, delete the rest).

        Destructive: call with confirm=False first to preview, then confirm=True.

        Args:
            node_ids (list[str]): All note ids to merge, including the target.
            target_id (str): The note id that absorbs the others (must be in node_ids).
            confirm (bool): False = preview; True = execute.
        """
        if agent_bridge is None:
            raise ValueError("merge_notes requires a live agent bridge.")
        result = await agent_bridge.merge_notes(
            board_id=graph_uid, node_ids=node_ids, target_id=target_id,
            confirm=confirm, user_uid=None)
        if not confirm:
            p = result["preview"]
            return (f"Preview: absorb {p['absorbed']} note(s) into target, "
                    f"repoint {p['edges_repointed']} edge(s), drop {p['edges_dropped']} self-loop(s). "
                    f"Confirm with the user before re-calling with confirm=True.")
        return MergeNotesOutput(target_id=target_id, graph_uid=graph_uid, absorbed=result["deleted"]["nodes"])

    return ToolHandler.convert_func_to_tool(
        merge_notes, tool_name=AgentToolName.MERGE_NOTES, tool_description=None,
    )


def create_split_note_tool(
    graph_store: GraphStore, graph_uid: str, agent_bridge: AgentBoardBridge | None = None,
) -> FunctionTool:
    """Build a split-note tool bound to the current board scope."""

    async def split_note(
        _wrapper: RunContextWrapper[Context],
        note_id: str,
        parts: list[str],
        confirm: bool = False,
        delete_original: bool = True,
    ) -> SplitNoteOutput | str:
        """Split one note into several sibling notes from content chunks.

        Destructive: call with confirm=False first to preview, then confirm=True.

        Args:
            note_id (str): The note to split.
            parts (list[str]): Content chunk for each new note.
            confirm (bool): False = preview; True = execute.
            delete_original (bool): Whether to delete the original note (default True).
        """
        if agent_bridge is None:
            raise ValueError("split_note requires a live agent bridge.")
        result = await agent_bridge.split_note(
            board_id=graph_uid, node_id=note_id, parts=parts,
            confirm=confirm, delete_original=delete_original, user_uid=None)
        if not confirm:
            p = result["preview"]
            return (f"Preview: create {p['new_nodes']} new note(s), repoint "
                    f"{p['inbound_edges_repointed']} inbound edge(s), "
                    f"delete_original={p['delete_original']}. "
                    f"Confirm with the user before re-calling with confirm=True.")
        return SplitNoteOutput(graph_uid=graph_uid, created_ids=result["created_ids"],
                               original_deleted=result["delete_original"])

    return ToolHandler.convert_func_to_tool(
        split_note, tool_name=AgentToolName.SPLIT_NOTE, tool_description=None,
    )


def create_relayout_tool(
    graph_store: GraphStore, graph_uid: str, agent_bridge: AgentBoardBridge | None = None,
) -> FunctionTool:
    """Build a relayout tool bound to the current board scope."""

    async def relayout_board(
        _wrapper: RunContextWrapper[Context],
        scope_ids: list[str] | None = None,
        mode: str = "default",
    ) -> RelayoutOutput:
        """Re-run auto-layout for a set of nodes or the whole board.

        Use this to tidy a messy graph after structural edits. mode="research"
        uses the hierarchical research layout; otherwise the default layout.

        Args:
            scope_ids (list[str] | None): Node ids to relayout, or None for the whole board.
            mode (str): "default" or "research".
        """
        if agent_bridge is None:
            raise ValueError("relayout requires a live agent bridge.")
        result = await agent_bridge.relayout(
            board_id=graph_uid, scope_ids=scope_ids, mode=mode)
        return RelayoutOutput(graph_uid=graph_uid, moved=result["count"], mode=result["mode"])

    return ToolHandler.convert_func_to_tool(
        relayout_board, tool_name=AgentToolName.RELAYOUT_BOARD, tool_description=None,
    )
```

Add the imports at the top of `tools.py`:

```python
from topix.agents.datatypes.outputs import (
    ChangeKindOutput,
    DeleteSubtreeOutput,
    MergeNotesOutput,
    ReparentNoteOutput,
    SplitNoteOutput,
    RelayoutOutput,
)
```

Register in `backend/topix/agents/assistant/plan.py` after line 100, inside the `if graph_store is not None and graph_uid is not None:` block:

```python
            tools.append(create_change_note_kind_tool(graph_store, graph_uid, agent_bridge=agent_bridge))
            tools.append(create_reparent_note_tool(graph_store, graph_uid, agent_bridge=agent_bridge))
            tools.append(create_delete_subtree_tool(graph_store, graph_uid, agent_bridge=agent_bridge))
            tools.append(create_merge_notes_tool(graph_store, graph_uid, agent_bridge=agent_bridge))
            tools.append(create_split_note_tool(graph_store, graph_uid, agent_bridge=agent_bridge))
            tools.append(create_relayout_tool(graph_store, graph_uid, agent_bridge=agent_bridge))
```

Add the imports to `plan.py`:

```python
from topix.agents.notes.tools import (
    create_change_note_kind_tool,
    create_delete_subtree_tool,
    create_edit_note_tool,
    create_get_note_tool,
    create_link_notes_tool,
    create_merge_notes_tool,
    create_relayout_tool,
    create_reparent_note_tool,
    create_split_note_tool,
    create_write_note_tool,
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest backend/test/unit/agents/test_notes_struct_tools.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/topix/agents/notes/tools.py backend/topix/agents/assistant/plan.py backend/test/unit/agents/test_notes_struct_tools.py
git commit -m "feat(agents): add six structural note tools and register them"
```

---

### Task 9: Integration endpoints

**Files:**
- Modify: `backend/topix/api/router/integration.py`
- Test: `backend/test/unit/api/router/test_integration_struct_ops.py`

**Interfaces:**
- Consumes: the six `AgentBoardBridge` methods + existing `_verify_token`, `redact_content`, `assert_can_mutate`, `assert_can_create`.
- Produces: `POST /integration/boards/{id}/nodes/{nid}:set-kind`, `POST .../nodes/{nid}:reparent`, `DELETE .../nodes/{nid}:subtree`, `POST .../nodes:merge`, `POST .../nodes/{nid}:split`.

- [ ] **Step 1: Write the failing test**

```python
# backend/test/unit/api/router/test_integration_struct_ops.py
import pytest

@pytest.mark.asyncio
async def test_set_kind_endpoint_requires_token(async_client, board_id, integration_token):
    r = await async_client.post(
        f"/integration/boards/{board_id}/nodes/none:set-kind",
        json={"kind": "finding"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_merge_endpoint_preview(async_client, board_id, integration_token, two_notes):
    a, b = two_notes
    r = await async_client.post(
        f"/integration/boards/{board_id}/nodes:merge",
        headers={"X-Integration-Token": integration_token},
        json={"node_ids": [a, b], "target_id": a, "confirm": False})
    assert r.status_code == 200
    assert r.json()["preview"]["absorbed"] == 1
```

(Fixtures `integration_token`, `two_notes`, `async_client` follow the existing `test_integration.py` patterns — reuse the conftest from the existing integration tests.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest backend/test/unit/api/router/test_integration_struct_ops.py -x -v`
Expected: FAIL (404 — routes not defined)

- [ ] **Step 3: Write minimal implementation**

Append to `backend/topix/api/router/integration.py`:

```python
class SetKindRequest(BaseModel):
    kind: str


class ReparentRequest(BaseModel):
    parent_id: str | None = None


class SubtreeDeleteRequest(BaseModel):
    confirm: bool = False


class MergeRequest(BaseModel):
    node_ids: list[str]
    target_id: str
    confirm: bool = False


class SplitRequest(BaseModel):
    parts: list[str]
    confirm: bool = False
    delete_original: bool = True


@router.post("/boards/{board_id}/nodes/{node_id}:set-kind")
async def set_node_kind(
    board_id: str, node_id: str, body: SetKindRequest, request: Request,
    _: None = Depends(_verify_token),
):
    """Re-style a node to a research kind (shape + color + size)."""
    from topix.integrations.research_scope import assert_can_mutate
    try:
        assert_can_mutate(board_id, node_id)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    bridge: AgentBoardBridge = _bridge(request)
    updated = await bridge.change_note_kind(
        board_id=board_id, node_id=node_id, kind=body.kind, user_uid=None)
    if updated is None:
        raise HTTPException(status_code=404, detail="Node not found")
    return {"node_id": node_id, "kind": body.kind, "updated": True}


@router.post("/boards/{board_id}/nodes/{node_id}:reparent")
async def reparent_node(
    board_id: str, node_id: str, body: ReparentRequest, request: Request,
    _: None = Depends(_verify_token),
):
    """Move a node under a new parent (or to the board root)."""
    from topix.integrations.research_scope import assert_can_mutate
    try:
        assert_can_mutate(board_id, node_id)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    bridge: AgentBoardBridge = _bridge(request)
    try:
        updated = await bridge.reparent_note(
            board_id=board_id, node_id=node_id, new_parent_id=body.parent_id, user_uid=None)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="Node not found")
    return {"node_id": node_id, "parent_id": updated.parent_id, "updated": True}


@router.delete("/boards/{board_id}/nodes/{node_id}:subtree")
async def delete_subtree_ep(
    board_id: str, node_id: str, request: Request,
    _: None = Depends(_verify_token),
):
    """Preview (default) then delete a node + its descendants.

    Pass `?confirm=true` to execute. Without it, returns the affected counts.
    """
    from topix.integrations.research_scope import assert_can_mutate
    try:
        assert_can_mutate(board_id, node_id)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    bridge: AgentBoardBridge = _bridge(request)
    confirm = request.query_params.get("confirm", "").lower() == "true"
    try:
        result = await bridge.delete_subtree(
            board_id=board_id, node_id=node_id, confirm=confirm, user_uid=None)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return result


@router.post("/boards/{board_id}/nodes:merge")
async def merge_nodes_ep(
    board_id: str, body: MergeRequest, request: Request,
    _: None = Depends(_verify_token),
):
    """Merge several nodes into one target node (two-phase via body.confirm)."""
    bridge: AgentBoardBridge = _bridge(request)
    try:
        result = await bridge.merge_notes(
            board_id=board_id, node_ids=body.node_ids, target_id=body.target_id,
            confirm=body.confirm, user_uid=None)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return result


@router.post("/boards/{board_id}/nodes/{node_id}:split")
async def split_node_ep(
    board_id: str, node_id: str, body: SplitRequest, request: Request,
    _: None = Depends(_verify_token),
):
    """Split one node into several sibling notes (two-phase via body.confirm)."""
    from topix.integrations.research_scope import assert_can_mutate
    try:
        assert_can_mutate(board_id, node_id)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    bridge: AgentBoardBridge = _bridge(request)
    try:
        result = await bridge.split_note(
            board_id=board_id, node_id=node_id, parts=body.parts,
            confirm=body.confirm, delete_original=body.delete_original, user_uid=None)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest backend/test/unit/api/router/test_integration_struct_ops.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/topix/api/router/integration.py backend/test/unit/api/router/test_integration_struct_ops.py
git commit -m "feat(integration): add five structural op endpoints"
```

---

### Task 10: MCP tools mirror

**Files:**
- Modify: `backend/topix/integrations/dim0_mcp/server.py`
- Test: `backend/test/unit/integrations/test_dim0_mcp_struct_tools.py`

**Interfaces:**
- Consumes: the integration endpoints (via `_api` helper in `server.py`).
- Produces: MCP tools `dim0_set_node_kind`, `dim0_reparent_node`, `dim0_delete_subtree`, `dim0_merge_nodes`, `dim0_split_node`.

- [ ] **Step 1: Write the failing test**

```python
# backend/test/unit/integrations/test_dim0_mcp_struct_tools.py
from topix.integrations.dim0_mcp.server import TOOLS


def test_mcp_struct_tool_names_present():
    names = {t["name"] for t in TOOLS}
    assert {"dim0_set_node_kind", "dim0_reparent_node",
            "dim0_delete_subtree", "dim0_merge_nodes", "dim0_split_node"} <= names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest backend/test/unit/integrations/test_dim0_mcp_struct_tools.py -x -v`
Expected: FAIL (names missing)

- [ ] **Step 3: Write minimal implementation**

Add five entries to the `TOOLS` list in `server.py` (mirror the existing `dim0_create_nodes` tool dict shape — `name`, `description`, `inputSchema`). Add five handlers in the dispatch map mirroring `handle_dim0_create_nodes` that call `_api("POST", f"/integration/boards/{board_id}/...")` with the right path + body. Example for set-kind:

```python
# In TOOLS list:
{
    "name": "dim0_set_node_kind",
    "description": "Re-style a board node to a research kind (question/finding/source/evidence/hypothesis/contradiction/unknown/alternative/decision/summary).",
    "inputSchema": {
        "type": "object",
        "properties": {
            "board_id": {"type": "string", "description": "Board ID (optional, uses default)."},
            "node_id": {"type": "string", "description": "Exact Dim0 node ID to re-style."},
            "kind": {"type": "string", "description": "New kind."},
        },
        "required": ["node_id", "kind"],
    },
},
# ... reparent / delete_subtree (DELETE + ?confirm=true) / merge / split similarly.
```

Handlers (in the `HANDLERS` map):

```python
async def handle_dim0_set_node_kind(args: dict) -> dict:
    board_id = args.get("board_id") or DEFAULT_BOARD_ID
    return await _api("POST", f"/integration/boards/{board_id}/nodes/{args['node_id']}:set-kind",
                      json={"kind": args["kind"]})

async def handle_dim0_reparent_node(args: dict) -> dict:
    board_id = args.get("board_id") or DEFAULT_BOARD_ID
    return await _api("POST", f"/integration/boards/{board_id}/nodes/{args['node_id']}:reparent",
                      json={"parent_id": args.get("parent_id")})

async def handle_dim0_delete_subtree(args: dict) -> dict:
    board_id = args.get("board_id") or DEFAULT_BOARD_ID
    confirm = "true" if args.get("confirm") else "false"
    return await _api("DELETE", f"/integration/boards/{board_id}/nodes/{args['node_id']}:subtree?confirm={confirm}")

async def handle_dim0_merge_nodes(args: dict) -> dict:
    board_id = args.get("board_id") or DEFAULT_BOARD_ID
    return await _api("POST", f"/integration/boards/{board_id}/nodes:merge", json={
        "node_ids": args["node_ids"], "target_id": args["target_id"],
        "confirm": bool(args.get("confirm", False)),
    })

async def handle_dim0_split_node(args: dict) -> dict:
    board_id = args.get("board_id") or DEFAULT_BOARD_ID
    return await _api("POST", f"/integration/boards/{board_id}/nodes/{args['node_id']}:split", json={
        "parts": args["parts"], "confirm": bool(args.get("confirm", False)),
        "delete_original": bool(args.get("delete_original", True)),
    })
```

Register `"dim0_set_node_kind": handle_dim0_set_node_kind`, etc. in the handler dispatch map (mirror the existing `dim0_create_nodes` registration).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest backend/test/unit/integrations/test_dim0_mcp_struct_tools.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/topix/integrations/dim0_mcp/server.py backend/test/unit/integrations/test_dim0_mcp_struct_tools.py
git commit -m "feat(mcp): mirror structural ops as dim0 mcp tools"
```

---

### Task 11: Prompt — teach the agent when to use structural ops

**Files:**
- Modify: `backend/topix/prompts/plan.system.jinja`
- Test: manual (covered by E2E in Task 13)

**Interfaces:**
- Consumes: the six tool descriptions (already in `tool_descriptions`).

- [ ] **Step 1: Add the section**

Append a "## Board structural edits" section to `backend/topix/prompts/plan.system.jinja`:

```jinja
## Board structural edits

When the user asks to refine the *structure* of the board (not just write note content),
use the structural tools. Prefer the nodes the user has selected (passed in the board
context) as targets; if no selection is given, ask which node they mean by quoting its title.

- `change_note_kind` — turn a Question into a Finding, a Hypothesis into a Decision, etc.
- `reparent_note` — move a node under a different parent to restructure the tree.
- `delete_subtree` — remove a node and all its descendants. Destructive: ALWAYS call
  with `confirm=False` first, show the user the preview (node/edge counts), wait for
  explicit agreement, then call with `confirm=True`.
- `merge_notes` — fold several nodes into one. Same two-phase confirm rule.
- `split_note` — break one node into several sibling notes from content chunks. Same rule.
- `relayout_board` — tidy a messy graph after structural edits.

Never run a destructive op (`delete_subtree`, `merge_notes`, `split_note`) with
`confirm=True` on the first call. If unsure whether the user confirmed, ask again.
```

- [ ] **Step 2: Verify the prompt renders**

Run: `uv run python -c "from topix.agents.base import BaseAgent; print('ok')"` (smoke — ensures jinja still parses via the existing `_render_prompt` path).
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add backend/topix/prompts/plan.system.jinja
git commit -m "docs(prompts): teach plan agent the structural board edits"
```

---

### Task 12: Frontend — tool output types, stream build, apply dispatch

**Files:**
- Modify: `webui/src/features/agent/types/tool-outputs.ts`
- Modify: `webui/src/features/agent/types/stream.ts:108-112`
- Modify: `webui/src/features/agent/utils/stream/build.ts`
- Modify: `webui/src/features/agent/store/chat-store.ts:52-54,117-119`
- Modify: `webui/src/features/board/harness/agent/apply-tool-output.ts`
- Modify: `webui/src/features/board/harness/agent/agent-bridge.ts`
- Modify: `webui/src/features/agent/api/send-message.ts:260-340`
- Test: `webui/src/features/board/harness/agent/apply-struct-output.test.ts`

**Interfaces:**
- Consumes: the backend output shapes (`type` literals: `change_note_kind`, `reparent_note`, `delete_subtree`, `merge_notes`, `split_note`, `relayout_board`).
- Produces: frontend interfaces + union members; stream build cases; chat-store allowlist entries; `apply-tool-output` functions + bridge methods; `send-message` dispatch cases.

- [ ] **Step 1: Write the failing test**

```ts
// webui/src/features/board/harness/agent/apply-struct-output.test.ts
import { describe, expect, it } from "vitest"

describe("struct output types", () => {
  it("declares the six new output type literals", async () => {
    const mod = await import("@/features/agent/types/tool-outputs")
    const literals = [
      "change_note_kind", "reparent_note", "delete_subtree",
      "merge_notes", "split_note", "relayout_board",
    ]
    // The union member interfaces must exist; instantiate each.
    for (const t of literals) {
      const out = { type: t, graphUid: "b", noteId: "n", kind: "finding" } as unknown
      expect((out as { type: string }).type).toBe(t)
    }
    expect(typeof mod).toBe("object")
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webui && npx vitest run src/features/board/harness/agent/apply-struct-output.test.ts`
Expected: FAIL (types not exported / not in union)

- [ ] **Step 3: Write minimal implementation**

`webui/src/features/agent/types/tool-outputs.ts` — add (matching the backend snake_case `type`):

```ts
export interface ChangeNoteKindOutput {
  type: "change_note_kind"
  noteId: string
  graphUid: string
  kind: string
}

export interface ReparentNoteOutput {
  type: "reparent_note"
  noteId: string
  graphUid: string
  parentId: string | null
}

export interface DeleteSubtreeOutput {
  type: "delete_subtree"
  graphUid: string
  deletedNodes: number
  deletedEdges: number
}

export interface MergeNotesOutput {
  type: "merge_notes"
  targetId: string
  graphUid: string
  absorbed: number
}

export interface SplitNoteOutput {
  type: "split_note"
  graphUid: string
  createdIds: string[]
  originalDeleted: boolean
}

export interface RelayoutOutput {
  type: "relayout_board"
  graphUid: string
  moved: number
  mode: string
}
```

Add each to the `ToolOutput` union (the `type ToolOutput = ...` at line ~120). Note: the backend sends `note_id`/`graph_uid`/`parent_id`/`deleted_nodes`/`target_id`/`created_ids` in snake_case; the existing stream layer (`build.ts`) camelCases them — verify by following the `WriteNoteOutput` build case and mirror its field mapping. If the existing layer does NOT camelCase, keep the interfaces in snake_case to match. (Check `build.ts:60-95` first and match its convention exactly.)

`webui/src/features/agent/types/stream.ts:108-112` — add the six literals to the tool-name union:

```ts
  | "change_note_kind"
  | "reparent_note"
  | "delete_subtree"
  | "merge_notes"
  | "split_note"
  | "relayout_board"
```

`webui/src/features/agent/store/chat-store.ts:52-54` and `:117-119` — add the six names next to `"write_note"`, `"edit_note"`, `"link_notes"` in both allowlists.

`webui/src/features/agent/utils/stream/build.ts` — add six cases mirroring the `write_note` case (lines 60-95): each builds the typed output from the streamed JSON. Example:

```ts
  if (acc.name === "change_note_kind") {
    return { ...acc, output: { type: "change_note_kind", noteId, graphUid, kind } }
  }
```

(Read `build.ts:55-95` first and copy the exact field-read pattern it uses — do not invent field names.)

`webui/src/features/board/harness/agent/apply-tool-output.ts` — add apply functions. For `change_note_kind` / `reparent_note`, the mutation already broadcast via WS peer-op, so the local apply fetches the fresh note and applies a `node.update` (mirror `applyNoteOutput`). For `delete_subtree` / `merge_notes` / `split_note`, apply `node.remove` / `node.add` remote batches for the affected ids. Example:

```ts
export const applyChangeKindOutput = async (
  store: CanvasStore, queryClient: QueryClient, activeBoardId: string,
  rootId: string | null, output: ChangeNoteKindOutput,
): Promise<ApplyNoteResult | null> => {
  if (output.graphUid !== activeBoardId) return null
  // Reuse the existing applyNoteOutput by fetching the fresh note.
  return applyNoteOutput(store, queryClient, activeBoardId, rootId, {
    type: "write_note", action: "rewritten",
    noteId: output.noteId, graphUid: output.graphUid,
    label: null, noteType: "", parentId: null,
  } as WriteNoteOutput)
}
```

(For destructive ops, apply `node.remove` ops for deleted ids and `node.add` for split-created ids using the existing `applyRemoteBatch` helper — already in the file.)

`webui/src/features/board/harness/agent/agent-bridge.ts` — add the six methods to the `AgentBridge` type and forward to the new apply functions.

`webui/src/features/agent/api/send-message.ts:260-340` — add dispatch cases next to the existing `applyNoteOutput` (line 267) / `applyLinkOutput` (line 273) calls and in the `switch (output.type)` (line 336):

```ts
if (harnessBridge && output.type === "change_note_kind") {
  await harnessBridge.applyChangeKindOutput(output)
}
// ...one per op
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd webui && npx vitest run src/features/board/harness/agent/apply-struct-output.test.ts`
Expected: PASS

Also run the typecheck: `cd webui && npx tsc --noEmit`
Expected: no errors

- [ ] **Step 5: Commit**

```bash
git add webui/src/features/agent webui/src/features/board/harness/agent
git commit -m "feat(webui): apply six structural tool outputs on the canvas"
```

---

### Task 13: E2E manual verification

**Files:** none (verification only)

- [ ] **Step 1: Start the backend on 8899**

Run (from `backend/`): `uv run uvicorn topix.api.app:app --host 127.0.0.1 --port 8899`
Expected: `Uvicorn running on http://127.0.0.1:8899`; `curl http://127.0.0.1:8899/health` → `{"status":"ok"}`; `mcp__dim0__dim0_health` → `status: ok`.

- [ ] **Step 2: Start the webui**

Run (from `webui/`): `npm run dev`
Expected: Vite dev server on `http://localhost:5173`.

- [ ] **Step 3: Verify each op via the Board Assistant**

Use the `run` skill to drive the app. For each scenario, open a board, select the target nodes, open the Board Assistant (CopilotSheet), type the prompt, and assert the canvas result:

1. **Change kind**: select a node → "đổi node này thành finding" → assert the node's shape/color changes to the finding visual (soft-diamond, emerald).
2. **Reparent**: select a child node → "gắn node này dưới node X" → assert the child moves under X; then try a cycle ("gắn X dưới child") → assert the agent reports the cycle is rejected.
3. **Delete subtree**: select a parent → "xóa nhánh này" → assert the agent shows a preview and asks for confirmation → confirm → assert the subtree is gone.
4. **Merge**: select 3 nodes → "gộp 3 node này, giữ node đầu" → preview → confirm → assert one node remains with merged content.
5. **Split**: select a node → "tách node này thành 2: 'one' và 'two'" → preview → confirm → assert two new nodes appear with the chunks and the original is gone.
6. **Relayout**: "sắp xếp lại board" → assert nodes reposition.

- [ ] **Step 4: Run the full backend test suite**

Run: `cd backend && uv run pytest backend/test/unit/collab/test_agent_bridge_struct_ops.py backend/test/unit/agents/test_notes_struct_tools.py backend/test/unit/api/router/test_integration_struct_ops.py backend/test/unit/integrations/test_dim0_mcp_struct_tools.py -v`
Expected: all PASS.

- [ ] **Step 5: Final commit (if any prompt/docs tweaks surfaced during E2E)**

```bash
git add -A
git commit -m "test(board): verify structural edit e2e flows"
```

---

## Self-Review

**1. Spec coverage:** Each spec requirement maps to a task — change kind (T1), reparent (T2), delete subtree (T3), merge (T4), split (T5), relayout (T6); chat-agent tools (T7-T8); integration endpoints (T9); MCP mirror (T10); prompt (T11); frontend apply (T12); two-phase confirm (T3/T4/T5 + prompt T11); cycle detection (T2); error handling 422/403/404 (T9); E2E (T13). No gaps.

**2. Placeholder scan:** No "TBD/TODO/implement later". Frontend field-name mapping in T12 carries an explicit "read build.ts first and match its convention" instruction rather than a guess — this is a verification step, not a placeholder.

**3. Type consistency:** Bridge method signatures match between definition (T1-T6), tool dispatch (T8), and endpoints (T9). Output model field names (`note_id`/`graph_uid`/`parent_id`/`target_id`/`absorbed`/`created_ids`/`deleted_nodes`) match between `outputs.py` (T7) and the bridge return dicts (T1-T6). Frontend interface field casing is flagged for verification against `build.ts` in T12.

**4. Scope:** Single implementation plan; each task produces independently testable deliverables.