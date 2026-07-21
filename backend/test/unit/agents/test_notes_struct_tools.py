from topix.agents.datatypes.outputs import (
    ChangeKindOutput,
    DeleteSubtreeOutput,
    MergeNotesOutput,
    RelayoutOutput,
    ReparentNoteOutput,
    SplitNoteOutput,
)


def test_struct_output_compact_repr():
    assert ChangeKindOutput(
        note_id="n1", graph_uid="b1", kind="finding").to_compact_repr() == 'kind=finding note_id="n1"'
    assert MergeNotesOutput(
        target_id="t1", graph_uid="b1", absorbed=2).to_compact_repr() == 'merged 2 into note_id="t1"'