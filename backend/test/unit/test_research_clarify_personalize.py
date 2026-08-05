"""Unit tests for personalized clarify (Claude CLI pass)."""
from topix.integrations.research_clarify import (
    ClarifyQuestion,
    ClarifyQuestionsOut,
    ClarifyRequest,
)


def test_clarify_request_accepts_board_context():
    """ClarifyRequest should accept board_id/mode/focus_node_ids for the Claude pass."""
    req = ClarifyRequest(
        topic="ref sáng tạo BHNT",
        stage="questions",
        board_id="b-1",
        mode="explore",
        focus_node_ids=["n-1", "n-2"],
    )
    assert req.board_id == "b-1"
    assert req.mode == "explore"
    assert req.focus_node_ids == ["n-1", "n-2"]


def test_clarify_request_board_context_optional():
    """Board context fields are optional (explore on no board still works)."""
    req = ClarifyRequest(topic="x")
    assert req.board_id is None
    assert req.mode is None
    assert req.focus_node_ids == []


def test_clarify_question_has_axis_default_other():
    """ClarifyQuestion.axis defaults to 'other' for UI grouping."""
    q = ClarifyQuestion(id="q1", question="Ngành hàng?")
    assert q.axis == "other"


def test_clarify_questions_out_clear_fields():
    """ClarifyQuestionsOut carries clear + rationale for the launcher clear branch."""
    out = ClarifyQuestionsOut(topic="x", questions=[], clear=True, rationale="đã rõ")
    assert out.clear is True
    assert out.rationale == "đã rõ"


from topix.integrations.research_clarify import build_clarify_prompt


def test_build_clarify_prompt_explore_vi_has_mode_and_instruction():
    """Explore prompt carries MODE, instruction, MCP read hint, clear/axis rules, vi output."""
    p = build_clarify_prompt(
        mode="explore", instruction="ref sáng tạo BHNT gia đình VN",
        focus_node_ids=[], language="vi",
    )
    assert "MODE=explore" in p
    assert "ref sáng tạo BHNT gia đình VN" in p
    assert "dim0_get_board" in p
    assert "clear" in p
    assert "axis" in p
    assert "Tiếng Việt" in p


def test_build_clarify_prompt_expand_includes_focus():
    """Expand prompt includes FOCUS_NODE_IDS so the agent scopes its gap check."""
    p = build_clarify_prompt(
        mode="expand", instruction="đào sâu",
        focus_node_ids=["n-1", "n-2"], language="vi",
    )
    assert "MODE=expand" in p
    assert "n-1" in p
    assert "n-2" in p


import json

import pytest

from topix.integrations import research_clarify as rc


class _FakeProc:
    """Minimal subprocess stub: stdout via communicate(), stderr unused."""

    def __init__(self, stdout_bytes: bytes, returncode: int = 0):
        self._out = stdout_bytes
        self.returncode = returncode
        self.stderr = None

    async def communicate(self):
        return self._out, b""


async def test_run_clarify_questions_claude_parses_questions(monkeypatch):
    """Claude pass returns structured questions with axis when not clear."""
    payload = json.dumps({
        "clear": False,
        "rationale": "thiếu ngành",
        "questions": [
            {"id": "q1", "question": "Ngành hàng?", "axis": "industry",
             "why": "bám category", "hint": "BHNT/F&B", "options": ["BHNT", "F&B"]},
        ],
    }).encode()
    monkeypatch.setattr(rc.shutil, "which", lambda name: "/fake/claude")

    async def fake_exec(*args, **kwargs):
        return _FakeProc(payload)

    monkeypatch.setattr(rc.asyncio, "create_subprocess_exec", fake_exec)
    out = await rc.run_clarify_questions_claude(
        board_id="b-1", mode="explore", instruction="ref sáng tạo",
        focus_node_ids=[], language="vi",
    )
    assert out.clear is False
    assert out.rationale == "thiếu ngành"
    assert len(out.questions) == 1
    assert out.questions[0].axis == "industry"
    assert out.model == "claude-cli"


async def test_run_clarify_questions_claude_clear_true(monkeypatch):
    """Claude pass with a clear brief returns clear=True and no questions."""
    payload = json.dumps({"clear": True, "rationale": "brief đủ rõ"}).encode()
    monkeypatch.setattr(rc.shutil, "which", lambda name: "/fake/claude")

    async def fake_exec(*args, **kwargs):
        return _FakeProc(payload)

    monkeypatch.setattr(rc.asyncio, "create_subprocess_exec", fake_exec)
    out = await rc.run_clarify_questions_claude(
        board_id="b-1", mode="explore", instruction="brief rất chi tiết",
        focus_node_ids=[], language="vi",
    )
    assert out.clear is True
    assert out.questions == []


async def test_run_clarify_questions_claude_raises_on_bad_json(monkeypatch):
    """Bad CLI output surfaces an exception so the caller can fall back."""
    monkeypatch.setattr(rc.shutil, "which", lambda name: "/fake/claude")

    async def fake_exec(*args, **kwargs):
        return _FakeProc(b"not json at all")

    monkeypatch.setattr(rc.asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(Exception):
        await rc.run_clarify_questions_claude(
            board_id="b-1", mode="explore", instruction="x",
            focus_node_ids=[], language="vi",
        )


async def test_run_clarify_questions_claude_raises_when_no_bin(monkeypatch):
    """Missing claude bin raises so the caller falls back to Ollama/static."""
    monkeypatch.setattr(rc.shutil, "which", lambda name: None)
    with pytest.raises(Exception):
        await rc.run_clarify_questions_claude(
            board_id="b-1", mode="explore", instruction="x",
            focus_node_ids=[], language="vi",
        )


async def test_run_clarify_questions_claude_raises_when_no_board(monkeypatch):
    """Without board_id the Claude pass cannot read the board and must raise."""
    monkeypatch.setattr(rc.shutil, "which", lambda name: "/fake/claude")
    with pytest.raises(Exception):
        await rc.run_clarify_questions_claude(
            board_id=None, mode="explore", instruction="x",
            focus_node_ids=[], language="vi",
        )


async def test_run_clarify_questions_uses_claude_when_available(monkeypatch):
    """run_clarify prefers the Claude pass when a board_id is present."""
    async def fake_claude(**kwargs):
        return ClarifyQuestionsOut(
            topic="", questions=[ClarifyQuestion(id="q1", question="x", axis="industry")],
            model="claude-cli", clear=False, rationale="g",
        )
    monkeypatch.setattr(rc, "run_clarify_questions_claude", fake_claude)
    out = await rc.run_clarify(ClarifyRequest(
        topic="ref", stage="questions", board_id="b-1", mode="explore", language="vi"))
    assert out.model == "claude-cli"
    assert out.questions[0].axis == "industry"


async def test_run_clarify_questions_falls_back_to_static(monkeypatch):
    """When Claude and Ollama both fail, the static fallback still returns questions."""
    async def boom(**kwargs):
        raise RuntimeError("no claude")
    monkeypatch.setattr(rc, "run_clarify_questions_claude", boom)

    async def boom_json(system, user):
        raise RuntimeError("no ollama")
    monkeypatch.setattr(rc, "_complete_json", boom_json)

    out = await rc.run_clarify(ClarifyRequest(topic="ref", stage="questions", language="vi"))
    assert out.model == "fallback"
    assert len(out.questions) >= 3
    assert all(
        q.axis in {"industry", "tone", "craft", "storyline", "scope", "other"}
        for q in out.questions
    )


async def test_run_clarify_questions_ollama_fallback_when_claude_fails(monkeypatch):
    """With no board_id, Claude is skipped and the Ollama path is used."""
    async def boom(**kwargs):
        raise RuntimeError("should not be called without board_id")
    monkeypatch.setattr(rc, "run_clarify_questions_claude", boom)
    captured = {}

    async def fake_json(system, user):
        captured["called"] = True
        return ({"questions": [
            {"id": "q1", "question": "Ngành?", "options": ["A", "B"]},
        ]}, "gemma")
    monkeypatch.setattr(rc, "_complete_json", fake_json)

    out = await rc.run_clarify(ClarifyRequest(topic="ref", stage="questions", language="vi"))
    assert captured.get("called") is True
    assert out.model == "gemma"
    assert out.questions[0].axis == "industry"


async def test_run_clarify_scope_is_deterministic_no_llama(monkeypatch):
    """Scope stage must fold topic+answers without invoking any LLM."""
    async def boom_json(system, user):
        raise AssertionError("scope must not call the LLM")
    monkeypatch.setattr(rc, "_complete_json", boom_json)

    out = await rc.run_clarify(ClarifyRequest(
        topic="ref BHNT", stage="scope",
        answers=[rc.ClarifyAnswer(id="q1", answer="BHNT gia đình VN"),
                 rc.ClarifyAnswer(id="q2", answer="bố mẹ 25-35")],
        language="vi"))
    assert isinstance(out, rc.ClarifyScopeOut)
    assert out.model == "fold"
    assert "BHNT" in out.scope_brief
    assert len(out.research_axes) >= 3
