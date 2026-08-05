"""Tests for the IMPROVE mode + VN quality rules (sub-project G)."""
from topix.integrations.research_runner import (
    ResearchMode,
    build_research_prompt,
    default_effort_for_mode,
    graph_writer_rules,
)


def test_improve_mode_default_effort_xhigh():
    assert default_effort_for_mode(ResearchMode.IMPROVE) == "xhigh"


def test_improve_prompt_preserves_not_rebuilds():
    """IMPROVE prompt must stress refine-not-delete and dim0_update_node."""
    p = build_research_prompt(
        board_id="b", mode=ResearchMode.IMPROVE, instruction="sửa văn phong",
        language="vi", session_id="s", focus_node_ids=["n1"], max_new_nodes=8,
    )
    assert "MODE=improve" in p
    assert "dim0_update_node" in p
    # Anti delete-and-rebuild emphasis
    assert "KHÔNG xóa-làm-mới" in p or "do NOT delete-and-rebuild" in p
    assert "n1" in p  # focus node referenced


def test_graph_writer_rules_include_vn_quality():
    r = graph_writer_rules(board_id="b", max_new_nodes=20, session_id="s", phase="explore")
    assert "VIETNAMESE QUALITY" in r
    assert "machine-translated" in r
    assert "PRESERVE" in r or "refine" in r


def test_graph_writer_rules_include_citation_integrity():
    r = graph_writer_rules(board_id="b", max_new_nodes=20, session_id="s", phase="explore")
    assert "CITATION INTEGRITY" in r
    assert "NEVER invent a URL" in r
    assert "dedupes by URL" in r


def test_graph_writer_rules_include_memory_and_video():
    r = graph_writer_rules(board_id="b", max_new_nodes=20, session_id="s", phase="explore")
    assert "MEMORY ACROSS ROUNDS" in r
    assert "rejected" in r
    assert "VIDEO REFERENCES" in r
    assert "youtube.com/watch" in r
    assert "vimeo.com" in r
