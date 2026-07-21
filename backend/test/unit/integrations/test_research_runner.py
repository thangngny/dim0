"""Unit tests for multi-mode research runner helpers."""

from topix.integrations.evidence_collect import derive_search_queries
from topix.integrations.research_meta import (
    merge_research_metadata,
    stamp_content,
)
from topix.integrations.research_runner import (
    ResearchMode,
    build_research_prompt,
    default_effort_for_mode,
)
from topix.integrations.research_scope import (
    assert_can_create,
    assert_can_mutate,
    begin_expand_scope,
    end_scope,
    note_created,
)


def test_effort_defaults():
    assert default_effort_for_mode(ResearchMode.EXPLORE) == "ultracode"
    assert default_effort_for_mode(ResearchMode.REFRAME) == "ultracode"
    assert default_effort_for_mode(ResearchMode.EXPAND) == "xhigh"
    assert default_effort_for_mode(ResearchMode.CRITIQUE) == "high"


def test_build_prompt_expand_includes_focus_and_evidence():
    p = build_research_prompt(
        board_id="b1",
        mode=ResearchMode.EXPAND,
        instruction="brand cảm động",
        language="vi",
        session_id="s1",
        focus_node_ids=["n1", "n2"],
        max_new_nodes=12,
        evidence_briefing="WEB_EVIDENCE: demo",
    )
    assert "MODE=expand" in p or "expand" in p.lower()
    assert "n1" in p
    assert "WEB_EVIDENCE" in p
    assert "GRAPHWRITER" in p
    assert "s1-expand" in p


def test_build_prompt_reframe():
    p = build_research_prompt(
        board_id="b1",
        mode=ResearchMode.REFRAME,
        instruction="chia theo storytelling",
        language="vi",
        session_id="s2",
        focus_node_ids=[],
        max_new_nodes=20,
    )
    assert "reframe" in p.lower()
    assert "taxonomy" in p.lower() or "TRỤC" in p


def test_meta_stamp():
    meta = merge_research_metadata(
        "source",
        {"brand": "Manulife", "citations": [{"title": "X", "url": "https://ex.com"}]},
        phase="expand",
        session_id="s",
    )
    text = stamp_content("Body here", meta)
    assert "### " in text or "Source" in text
    assert "expand" in text
    assert "Manulife" in text
    assert "https://ex.com" in text
    # idempotent-ish when already stamped
    again = stamp_content(text, meta)
    assert again.startswith("### ")


def test_expand_scope_guard():
    board = "board-scope-test"
    end_scope(board)
    begin_expand_scope(board, "sess", ["focus1"], max_new_nodes=2)
    assert_can_mutate(board, "focus1")  # ok
    try:
        assert_can_mutate(board, "other")
        raised = False
    except ValueError:
        raised = True
    assert raised
    assert_can_create(board, 2)
    note_created(board, ["new1"])
    note_created(board, ["new2"])
    try:
        assert_can_create(board, 1)
        over = False
    except ValueError:
        over = True
    assert over
    end_scope(board, "sess")
    # no scope → free mutate
    assert_can_mutate(board, "other")


def test_derive_queries():
    qs = derive_search_queries(
        "chiến dịch truyền thông thương hiệu bảo hiểm cảm động",
        language="vi",
    )
    assert len(qs) >= 1
    assert any("bảo hiểm" in q or "insurance" in q.lower() or "campaign" in q.lower() for q in qs)
