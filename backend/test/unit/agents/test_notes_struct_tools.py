import json

import pytest

from agents.tool_context import ToolContext

from topix.agents.datatypes.context import Context
from topix.agents.datatypes.outputs import (
    ChangeKindOutput,
    DeleteSubtreeOutput,
    MergeNotesOutput,
    RelayoutOutput,
    ReparentNoteOutput,
    SplitNoteOutput,
)
from topix.agents.notes.tools import create_change_note_kind_tool

# Reuse the in-memory GraphStore + AgentBoardBridge fixtures shared with
# the bridge-level structural-op tests. The bridge's `change_note_kind`
# reads the note back via `get_nodes` and deep-merges patches via
# `patch_note`, so the fake store must actually persist + return notes —
# the `_MemGraphStore` in the collab test module already provides that.
from test.unit.collab.test_agent_bridge_struct_ops import (  # noqa: E402
    _make_note,
    agent_bridge,
    board_id,
    graph_store,
    room_registry,
)


def test_struct_output_compact_repr():
    assert ChangeKindOutput(
        note_id="n1", graph_uid="b1", kind="finding").to_compact_repr() == 'kind=finding note_id="n1"'
    assert MergeNotesOutput(
        target_id="t1", graph_uid="b1", absorbed=2).to_compact_repr() == 'merged 2 into note_id="t1"'


def _ctx() -> ToolContext[Context]:
    """Build a ToolContext matching the SDK's `on_invoke_tool` entry point.

    The agents SDK's `FunctionTool.on_invoke_tool` expects a `ToolContext`
    (a `RunContextWrapper` subclass carrying `tool_name` / `tool_call_id` /
    `tool_arguments`). The brief's `RunContextWrapper(Context())` form lacks
    those attributes and falls back to the SDK's error-string path, so we
    mirror the existing note-tool test pattern (`test_write_note_mini_app`).
    """
    return ToolContext(
        context=Context(),
        tool_name="change_note_kind",
        tool_call_id="test-call-id",
        tool_arguments="{}",
    )


@pytest.mark.asyncio
async def test_change_kind_tool_dispatches_to_bridge(graph_store, board_id, agent_bridge):
    """The change-kind tool routes through the bridge's `change_note_kind`.

    Invokes the wrapped FunctionTool via the SDK's `on_invoke_tool` with a
    JSON args payload (note_id + kind) and asserts the result is a
    `ChangeKindOutput` shaped by the bridge's return value — verifying real
    dispatch through the bridge, not a mock.
    """
    note = await _make_note(graph_store, board_id, label="Q", content="x")
    tool = create_change_note_kind_tool(graph_store, board_id, agent_bridge)
    out = await tool.on_invoke_tool(
        _ctx(),
        json.dumps({"note_id": note.id, "kind": "finding"}),
    )
    assert isinstance(out, ChangeKindOutput)
    assert out.note_id == note.id
    assert out.kind == "finding"
    assert out.graph_uid == board_id