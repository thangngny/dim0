# Ask-back cá nhân hóa (Clarify pass Claude CLI) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Thay clarify gate generic (Ollama template) bằng Claude CLI pass cá nhân hóa: agent đọc board+instruction, tự đánh giá độ rõ, hỏi 0–4 câu theo gap (gắn `axis`), fold answers deterministic thành scope_brief cho research runner (runner zero-change).

**Architecture:** Hai lần Claude CLI tách rời — clarify pass (mới, prompt nhỏ, `--effort high`, đọc board qua MCP) → research pass (hiện tại, instruction = scope_brief). `stage=scope` trở thành fold deterministic (bỏ LLM thứ 3). Fallback chain: Claude CLI → Ollama `_complete_json` (sẵn có) → `_fallback_questions` (static). Launcher UI (vanilla JS trong `launcher.html`) thêm nhánh `clear:true` + nhóm câu hỏi theo `axis`.

**Tech Stack:** Python 3 / FastAPI / Pydantic v2 (backend `backend/topix/integrations/research_clarify.py`, `backend/topix/api/router/integration.py`); pytest (`backend/test/unit`, `backend/test/integration`); vanilla JS + HTML (`webui/public/launcher.html`).

## Global Constraints

- Commit format: `type(scope): message` — scope bắt buộc, lowercase, imperative, no trailing period. One commit = one logical change. Scope dùng `clarify`/`integration`/`launcher`.
- Backend Python: type hints đầy đủ, docstring 1–3 dòng cho hàm mới/sửa (intent+behavior, không line-by-line).
- Frontend `launcher.html` là vanilla JS (không TS) — giữ style hiện tại.
- Không đổi `research_runner.py` (runner zero-change).
- Giữ Ollama path làm fallback (không xóa `_complete_json` / `_fallback_questions`).
- Node kinds/axes vocabulary cố định: `storyline|tone|craft|industry|scope|other`.
- `claude` CLI invoked qua `shutil.which("claude")` + `asyncio.create_subprocess_exec`, env pin `DIM0_DEFAULT_BOARD_ID` — mirror `research_runner.stream_research_claude`.

---

## File Structure

- **Modify** `backend/topix/integrations/research_clarify.py` — thêm `build_clarify_prompt`, `run_clarify_questions_claude`; sửa schema + `run_clarify`.
- **Modify** `backend/topix/api/router/integration.py` — forward `board_id/mode/focus_node_ids` (schema đã nhận nên endpoint chỉ comment + forward; thực tế không cần đổi logic, chỉ docstring).
- **Modify** `webui/public/launcher.html` — `doClarifyQuestions` (clear branch + payload), `renderQuestions` (group by axis).
- **Create** `backend/test/unit/test_research_clarify_personalize.py` — unit tests.
- **Create** `backend/test/integration/test_clarify_endpoint.py` — endpoint forward test.

---

## Task 1: Schema additions

**Files:**
- Modify: `backend/topix/integrations/research_clarify.py` (classes `ClarifyRequest`, `ClarifyQuestion`, `ClarifyQuestionsOut`)
- Test: `backend/test/unit/test_research_clarify_personalize.py`

**Interfaces:**
- Produces: `ClarifyRequest.board_id: str | None`, `ClarifyRequest.mode: str | None`, `ClarifyRequest.focus_node_ids: list[str]`; `ClarifyQuestion.axis: str`; `ClarifyQuestionsOut.clear: bool`, `ClarifyQuestionsOut.rationale: str`.

- [ ] **Step 1: Write the failing test**

```python
# backend/test/unit/test_research_clarify_personalize.py
"""Unit tests for personalized clarify (Claude CLI pass)."""
from topix.integrations.research_clarify import (
    ClarifyQuestion,
    ClarifyQuestionsOut,
    ClarifyRequest,
)


def test_clarify_request_accepts_board_context():
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
    req = ClarifyRequest(topic="x")
    assert req.board_id is None
    assert req.mode is None
    assert req.focus_node_ids == []


def test_clarify_question_has_axis_default_other():
    q = ClarifyQuestion(id="q1", question="Ngành hàng?")
    assert q.axis == "other"


def test_clarify_questions_out_clear_fields():
    out = ClarifyQuestionsOut(topic="x", questions=[], clear=True, rationale="đã rõ")
    assert out.clear is True
    assert out.rationale == "đã rõ"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest test/unit/test_research_clarify_personalize.py -v`
Expected: FAIL — `ClarifyRequest` không nhận `board_id`, `ClarifyQuestion` không có `axis`, `ClarifyQuestionsOut` không có `clear`/`rationale`.

- [ ] **Step 3: Write minimal implementation**

In `backend/topix/integrations/research_clarify.py`, edit `ClarifyRequest`:

```python
class ClarifyRequest(BaseModel):
    """Clarify pipeline request.

    - stage=questions: Claude CLI reads board + instruction, returns 0–4
      personalized questions (or clear=true) by real gaps — not generic.
    - stage=scope: deterministic fold of topic + answers into scope brief.
    """

    topic: str = Field(..., min_length=1, max_length=2000)
    language: str = Field(default="vi", description="'vi' or 'en'")
    stage: Literal["questions", "scope"] = "questions"
    answers: list[ClarifyAnswer] = Field(default_factory=list)
    board_id: str | None = Field(default=None, description="Board for Claude CLI to read.")
    mode: str | None = Field(default=None, description="explore|reframe|expand|critique.")
    focus_node_ids: list[str] = Field(default_factory=list)
```

Edit `ClarifyQuestion`:

```python
class ClarifyQuestion(BaseModel):
    """A single clarifying question shown in the launcher form."""

    id: str
    question: str
    why: str = ""
    hint: str = ""
    options: list[str] = Field(default_factory=list)
    axis: str = Field(
        default="other",
        description="storyline|tone|craft|industry|scope|other — for UI grouping.",
    )
```

Edit `ClarifyQuestionsOut`:

```python
class ClarifyQuestionsOut(BaseModel):
    """Response for stage=questions."""

    stage: Literal["questions"] = "questions"
    topic: str
    questions: list[ClarifyQuestion]
    model: str = ""
    clear: bool = Field(default=False, description="True when agent says brief is already clear (0 questions).")
    rationale: str = Field(default="", description="Why clear/unclear — shown in launcher.")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest test/unit/test_research_clarify_personalize.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/topix/integrations/research_clarify.py backend/test/unit/test_research_clarify_personalize.py
git commit -m "feat(clarify): add board context + axis + clear fields to clarify schema"
```

---

## Task 2: `build_clarify_prompt`

**Files:**
- Modify: `backend/topix/integrations/research_clarify.py` (add `build_clarify_prompt`)
- Test: `backend/test/unit/test_research_clarify_personalize.py` (append)

**Interfaces:**
- Produces: `build_clarify_prompt(*, mode, instruction, focus_node_ids, language) -> str` — prompt text for Claude CLI clarify pass. (board_id is pinned via env, not in prompt body.)

- [ ] **Step 1: Write the failing test**

Append to `backend/test/unit/test_research_clarify_personalize.py`:

```python
from topix.integrations.research_clarify import build_clarify_prompt


def test_build_clarify_prompt_explore_vi_has_mode_and_instruction():
    p = build_clarify_prompt(
        mode="explore", instruction="ref sáng tạo BHNT gia đình VN",
        focus_node_ids=[], language="vi",
    )
    assert "MODE=explore" in p
    assert "ref sáng tạo BHNT gia đình VN" in p
    assert "dim0_get_board" in p
    assert "clear" in p
    assert "axis" in p
    # vi instruction → asks for Vietnamese output
    assert "tiếng Việt" in p or "Vietnamese" in p


def test_build_clarify_prompt_expand_includes_focus():
    p = build_clarify_prompt(
        mode="expand", instruction="đào sâu",
        focus_node_ids=["n-1", "n-2"], language="vi",
    )
    assert "MODE=expand" in p
    assert "n-1" in p
    assert "n-2" in p
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest test/unit/test_research_clarify_personalize.py::test_build_clarify_prompt_explore_vi_has_mode_and_instruction -v`
Expected: FAIL — `build_clarify_prompt` không tồn tại (ImportError).

- [ ] **Step 3: Write minimal implementation**

Add to `backend/topix/integrations/research_clarify.py` (after `build_research_prompt`-equivalent region — place near top of helpers, after `_default_clarify_model`):

```python
def build_clarify_prompt(
    *,
    mode: str | None,
    instruction: str,
    focus_node_ids: list[str],
    language: str,
) -> str:
    """Build the Claude CLI clarify pass prompt.

    Agent reads the board via MCP (DIM0_DEFAULT_BOARD_ID pinned by caller),
    self-assesses clarity, and returns JSON with clear/flag + 0–4 questions
    tagged by axis. Output is JSON only — no prose, no fence.
    """
    mode_s = (mode or "explore").strip() or "explore"
    focus = ", ".join(focus_node_ids[:20]) if focus_node_ids else "(none)"
    lang_vi = (language or "vi").lower().startswith("vi")
    out_lang = "Tiếng Việt" if lang_vi else "English"

    return (
        "You are the Dim0 research lead about to run a research pass.\n\n"
        f"MODE={mode_s}\n"
        f"FOCUS_NODE_IDS={focus}\n"
        f"INSTRUCTION:\n{instruction.strip()[:4000]}\n\n"
        "STEP 1 — Read the board first: call dim0_get_board and dim0_list_nodes "
        "(board_id is pinned for you). For MODE=expand/reframe/critique, pay "
        "attention to FOCUS_NODE_IDS and their neighbors.\n"
        "STEP 2 — Assess: is INSTRUCTION + current board state clear enough to "
        f"start MODE={mode_s} WITHOUT guessing?\n\n"
        "OUTPUT RULES (mandatory):\n"
        "- Output ONLY a single JSON object. No prose, no markdown fence. "
        f"First character must be '{{'. All text in {out_lang}.\n"
        "- If clear enough: {\"clear\": true, \"rationale\": \"<1 line why>\"}.\n"
        "- If NOT clear: {\"clear\": false, \"rationale\": \"<1 line>\", "
        "\"questions\": [ {\"id\":\"q1\",\"question\":\"...\",\"why\":\"...\","
        "\"hint\":\"...\",\"options\":[...],\"axis\":\"...\"} ]}.\n"
        "- Ask ONLY about real gaps you cannot answer from the board+INSTRUCTION. "
        "1 to 4 questions max. Never ask generics already answerable.\n"
        "- Each question targets ONE concrete gap and has an `axis` ∈ "
        "storyline|tone|craft|industry|scope|other.\n"
        "- options: 0–4 quick picks (user can still type freely).\n"
        "- For explore on an empty board: questions come from the brief "
        "(industry traits, audience, refs, scope) — NOT generic.\n"
        "- For expand/reframe/critique: questions must reference specific gaps "
        "in the existing board / focus cluster.\n"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest test/unit/test_research_clarify_personalize.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/topix/integrations/research_clarify.py backend/test/unit/test_research_clarify_personalize.py
git commit -m "feat(clarify): add build_clarify_prompt for claude cli pass"
```

---

## Task 3: `run_clarify_questions_claude` (subprocess + JSON parse)

**Files:**
- Modify: `backend/topix/integrations/research_clarify.py` (add `run_clarify_questions_claude`)
- Test: `backend/test/unit/test_research_clarify_personalize.py` (append)

**Interfaces:**
- Consumes: `build_clarify_prompt`, `_extract_json_object`, env `DIM0_*`, `shutil.which`, `asyncio.create_subprocess_exec`.
- Produces: `async run_clarify_questions_claude(*, board_id, mode, instruction, focus_node_ids, language) -> ClarifyQuestionsOut`. Raises on any failure (caller falls back).

- [ ] **Step 1: Write the failing test**

Append:

```python
import asyncio
import json
from unittest.mock import patch

import pytest

from topix.integrations import research_clarify as rc


class _FakeProc:
    def __init__(self, stdout_bytes: bytes, returncode: int = 0, stderr_bytes: bytes = b""):
        self._out = stdout_bytes
        self.returncode = returncode
        self.stderr = None

    class _Stream:
        def __init__(self, data: bytes):
            self._data = data
        def __aiter__(self):
            self._lines = self._data.splitlines(keepends=True)
            self._i = 0
            return self
        async def __anext__(self):
            if self._i >= len(self._lines):
                raise StopAsyncIteration
            line = self._lines[self._i]
            self._i += 1
            return line

    @property
    def stdout(self):
        return self._FakeStream(self._out)


@pytest.mark.asyncio
async def test_run_clarify_questions_claude_parses_questions(monkeypatch):
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


@pytest.mark.asyncio
async def test_run_clarify_questions_claude_clear_true(monkeypatch):
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


@pytest.mark.asyncio
async def test_run_clarify_questions_claude_raises_on_bad_json(monkeypatch):
    monkeypatch.setattr(rc.shutil, "which", lambda name: "/fake/claude")
    async def fake_exec(*args, **kwargs):
        return _FakeProc(b"not json at all")
    monkeypatch.setattr(rc.asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(Exception):
        await rc.run_clarify_questions_claude(
            board_id="b-1", mode="explore", instruction="x",
            focus_node_ids=[], language="vi",
        )


@pytest.mark.asyncio
async def test_run_clarify_questions_claude_raises_when_no_bin(monkeypatch):
    monkeypatch.setattr(rc.shutil, "which", lambda name: None)
    with pytest.raises(Exception):
        await rc.run_clarify_questions_claude(
            board_id="b-1", mode="explore", instruction="x",
            focus_node_ids=[], language="vi",
        )
```

Note: ensure `pytest-asyncio` is installed; if not, use `asyncio.run` wrapper in test instead. Check `backend` deps first — if `pytest-asyncio` missing, replace `@pytest.mark.asyncio` tests with `def test_...(): asyncio.run(coro())`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest test/unit/test_research_clarify_personalize.py -k run_clarify_questions_claude -v`
Expected: FAIL — `run_clarify_questions_claude` không tồn tại.

- [ ] **Step 3: Write minimal implementation**

Add imports near top of `research_clarify.py` (after existing imports):

```python
import asyncio
import shutil
```

Add function (after `build_clarify_prompt`):

```python
_CLARIFY_TIMEOUT = 90.0


def _repo_root() -> str:
    """Resolve monorepo root (parent of backend/), mirrors research_runner."""
    import os
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


async def run_clarify_questions_claude(
    *,
    board_id: str | None,
    mode: str | None,
    instruction: str,
    focus_node_ids: list[str],
    language: str,
) -> ClarifyQuestionsOut:
    """Spawn Claude CLI clarify pass; parse JSON. Raise on any failure.

    Board is read by the agent via MCP (DIM0_DEFAULT_BOARD_ID pinned).
    Caller must fall back to Ollama/static on exception.
    """
    import os
    import uuid

    claude_bin = shutil.which("claude")
    if not claude_bin:
        raise RuntimeError("claude CLI not found on PATH")

    if not board_id:
        raise RuntimeError("board_id required for Claude CLI clarify pass")

    prompt = build_clarify_prompt(
        mode=mode, instruction=instruction,
        focus_node_ids=focus_node_ids, language=language,
    )

    env = {**os.environ}
    env["DIM0_BASE_URL"] = env.get("DIM0_BASE_URL") or "http://localhost:8899"
    env["DIM0_DEFAULT_BOARD_ID"] = board_id
    if env.get("DIM0_INTEGRATION_TOKEN") is None and os.getenv("DIM0_INTEGRATION_TOKEN"):
        env["DIM0_INTEGRATION_TOKEN"] = os.getenv("DIM0_INTEGRATION_TOKEN", "")

    proc = await asyncio.create_subprocess_exec(
        claude_bin,
        "--dangerously-skip-permissions",
        "--effort", "high",
        "-p", prompt,
        "--output-format", "text",
        cwd=_repo_root(),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_bytes, _stderr = await asyncio.wait_for(
            proc.communicate(), timeout=_CLARIFY_TIMEOUT,
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        raise RuntimeError("claude CLI clarify pass timed out")

    text = (stdout_bytes or b"").decode("utf-8", errors="replace")
    data = _extract_json_object(text)

    clear = bool(data.get("clear"))
    rationale = str(data.get("rationale") or "").strip()
    qs_raw = data.get("questions") or []
    questions: list[ClarifyQuestion] = []
    for i, item in enumerate(qs_raw[:4]):
        if not isinstance(item, dict):
            continue
        qtext = str(item.get("question") or "").strip()
        if not qtext:
            continue
        opts = item.get("options") or []
        if not isinstance(opts, list):
            opts = []
        questions.append(ClarifyQuestion(
            id=str(item.get("id") or f"q{i + 1}"),
            question=qtext,
            why=str(item.get("why") or ""),
            hint=str(item.get("hint") or ""),
            options=[str(o) for o in opts[:4] if str(o).strip()],
            axis=str(item.get("axis") or "other").strip() or "other",
        ))
    return ClarifyQuestionsOut(
        topic="", questions=questions, model="claude-cli",
        clear=clear, rationale=rationale,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest test/unit/test_research_clarify_personalize.py -k run_clarify_questions_claude -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/topix/integrations/research_clarify.py backend/test/unit/test_research_clarify_personalize.py
git commit -m "feat(clarify): spawn claude cli clarify pass and parse questions"
```

---

## Task 4: Rewrite `run_clarify` stage=questions with fallback chain

**Files:**
- Modify: `backend/topix/integrations/research_clarify.py` (`run_clarify` questions branch)
- Test: `backend/test/unit/test_research_clarify_personalize.py` (append)

**Interfaces:**
- Consumes: `run_clarify_questions_claude`, `_complete_json`, `_questions_system`, `_fallback_questions`, `_infer_axis_for_fallback` (new helper).
- Produces: `run_clarify(body)` returns `ClarifyQuestionsOut` with `clear`/`axis` populated, falling back Claude → Ollama → static.

- [ ] **Step 1: Write the failing test**

Append:

```python
@pytest.mark.asyncio
async def test_run_clarify_questions_uses_claude_when_available(monkeypatch):
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


@pytest.mark.asyncio
async def test_run_clarify_questions_falls_back_when_claude_raises(monkeypatch):
    async def boom(**kwargs):
        raise RuntimeError("no claude")
    monkeypatch.setattr(rc, "run_clarify_questions_claude", boom)
    # also force Ollama to fail so we reach static fallback
    async def boom_json(system, user):
        raise RuntimeError("no ollama")
    monkeypatch.setattr(rc, "_complete_json", boom_json)
    out = await rc.run_clarify(ClarifyRequest(topic="ref", stage="questions", language="vi"))
    assert out.model == "fallback"
    assert len(out.questions) >= 3
    # fallback questions must carry an axis
    assert all(q.axis in {"industry", "tone", "craft", "storyline", "scope", "other"} for q in out.questions)


@pytest.mark.asyncio
async def test_run_clarify_questions_ollama_fallback_when_no_board(monkeypatch):
    # no board_id → Claude path raises → Ollama path used (mocked)
    async def boom(**kwargs):
        raise RuntimeError("no board")
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
    assert out.questions[0].axis == "other"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest test/unit/test_research_clarify_personalize.py -k run_clarify -v`
Expected: FAIL — `run_clarify` chưa gọi `run_clarify_questions_claude`, fallback questions chưa có `axis`.

- [ ] **Step 3: Write minimal implementation**

Add a small helper to assign axes to fallback questions (heuristic on text):

```python
def _infer_axis_for_fallback(qtext: str) -> str:
    """Best-effort axis tag for static-fallback questions (VN keywords)."""
    t = qtext.lower()
    if any(k in t for k in ("ngành", "category", "industry")):
        return "industry"
    if any(k in t for k in ("storyline", "cốt truyện", "tứ truyện", "kể", "arc")):
        return "storyline"
    if any(k in t for k in ("tone", "mood", "màu", "nhịp", "vibe")):
        return "tone"
    if any(k in t for k in ("thủ pháp", "đồ họa", "3d", "motion", "kỹ thuật", "thuật")):
        return "craft"
    if any(k in t for k in ("phạm vi", "ngoài", "out of", "scope", "không cần")):
        return "scope"
    return "other"
```

Rewrite the `if body.stage == "questions":` branch of `run_clarify`:

```python
    if body.stage == "questions":
        # 1) Claude CLI pass (personalized, reads board) — needs board_id.
        if body.board_id:
            try:
                return await run_clarify_questions_claude(
                    board_id=body.board_id, mode=body.mode,
                    instruction=topic, focus_node_ids=body.focus_node_ids,
                    language=lang,
                )
            except Exception as exc:
                logger.warning("clarify claude pass failed, falling back: %s", exc)
        # 2) Ollama fallback (existing path).
        try:
            data, model = await _complete_json(
                _questions_system(lang),
                f"TOPIC:\n{topic}\n\nGenerate clarifying questions JSON now.",
            )
            qs_raw = data.get("questions") or []
            questions: list[ClarifyQuestion] = []
            for i, item in enumerate(qs_raw[:7]):
                if not isinstance(item, dict):
                    continue
                qid = str(item.get("id") or f"q{i + 1}")
                qtext = str(item.get("question") or "").strip()
                if not qtext:
                    continue
                opts = item.get("options") or []
                if not isinstance(opts, list):
                    opts = []
                questions.append(ClarifyQuestion(
                    id=qid, question=qtext,
                    why=str(item.get("why") or ""),
                    hint=str(item.get("hint") or ""),
                    options=[str(o) for o in opts[:4] if str(o).strip()],
                    axis=str(item.get("axis") or _infer_axis_for_fallback(qtext)),
                ))
            if len(questions) < 1:
                raise ValueError("too few questions from model")
            return ClarifyQuestionsOut(
                topic=topic, questions=questions, model=model,
                clear=False, rationale="",
            )
        except Exception as exc:
            logger.warning("clarify questions LLM failed, using fallback: %s", exc)
        # 3) Static fallback.
        fb = _fallback_questions(topic, lang)
        return ClarifyQuestionsOut(
            topic=topic,
            questions=[q.model_copy(update={"axis": _infer_axis_for_fallback(q.question)})
                        for q in fb],
            model="fallback", clear=False, rationale="static fallback",
        )
```

(Note: relax the Ollama "too few" threshold from `< 3` to `< 1` so a single clear answer isn't rejected.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest test/unit/test_research_clarify_personalize.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add backend/topix/integrations/research_clarify.py backend/test/unit/test_research_clarify_personalize.py
git commit -m "feat(clarify): wire claude->ollama->static fallback chain in run_clarify"
```

---

## Task 5: Deterministic scope fold (stage=scope)

**Files:**
- Modify: `backend/topix/integrations/research_clarify.py` (`run_clarify` scope branch)
- Test: `backend/test/unit/test_research_clarify_personalize.py` (append)

**Interfaces:**
- Produces: `run_clarify` scope branch returns `ClarifyScopeOut` via `_fallback_scope` (deterministic, no LLM).

- [ ] **Step 1: Write the failing test**

Append:

```python
@pytest.mark.asyncio
async def test_run_clarify_scope_is_deterministic_no_llama(monkeypatch):
    # If scope ever called _complete_json, this would explode.
    async def boom_json(system, user):
        raise AssertionError("scope must not call LLM")
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest test/unit/test_research_clarify_personalize.py::test_run_clarify_scope_is_deterministic_no_llama -v`
Expected: FAIL — scope branch vẫn gọi `_complete_json`.

- [ ] **Step 3: Write minimal implementation**

Replace the existing scope branch (the `# stage == scope` block ending `run_clarify`) with:

```python
    # stage == scope — deterministic fold (no LLM).
    fb = _fallback_scope(topic, body.answers, lang)
    return fb.model_copy(update={"model": "fold"})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest test/unit/test_research_clarify_personalize.py::test_run_clarify_scope_is_deterministic_no_llama -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/topix/integrations/research_clarify.py backend/test/unit/test_research_clarify_personalize.py
git commit -m "feat(clarify): make scope stage a deterministic fold without llm"
```

---

## Task 6: Endpoint forward + integration test

**Files:**
- Modify: `backend/topix/api/router/integration.py` (`research_clarify` docstring only — schema already forwards)
- Test: `backend/test/integration/test_clarify_endpoint.py`

**Interfaces:**
- Produces: `/integration/research/clarify` accepts `board_id/mode/focus_node_ids` and forwards to `run_clarify`.

- [ ] **Step 1: Write the failing test**

```python
# backend/test/integration/test_clarify_endpoint.py
"""Integration test: /integration/research/clarify forwards board context."""
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("DIM0_INTEGRATION_TOKEN", "test-token")
    from topix.api.main import app  # adjust import if app factory differs
    return TestClient(app)


def test_clarify_questions_forwards_board_context(client, monkeypatch):
    captured = {}

    class _Out:
        def model_dump(self):
            return {"questions": [], "clear": True, "model": "claude-cli"}

    async def fake_run_clarify(body):
        captured["body"] = body
        return _Out()

    monkeypatch.setattr("topix.api.router.integration.run_clarify", fake_run_clarify)
    r = client.post(
        "/integration/research/clarify",
        headers={"X-Integration-Token": "test-token"},
        json={"topic": "ref", "stage": "questions",
              "board_id": "b-1", "mode": "explore", "focus_node_ids": ["n1"]},
    )
    assert r.status_code == 200
    body = captured["body"]
    assert body.board_id == "b-1"
    assert body.mode == "explore"
    assert body.focus_node_ids == ["n1"]
```

Note: verify the app import path (`topix.api.main:app`) before writing — if different, adjust the fixture. The endpoint returns `run_clarify` result directly (a Pydantic model) — FastAPI serializes via `model_dump`. Confirm by checking another integration endpoint's return shape.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest test/integration/test_clarify_endpoint.py -v`
Expected: FAIL — likely app import path or `run_clarify` not patched at correct path.

- [ ] **Step 3: Write minimal implementation**

In `backend/topix/api/router/integration.py`, update the docstring (schema already forwards new fields automatically):

```python
@router.post("/research/clarify")
async def research_clarify(
    body: ClarifyRequest,
    _: None = Depends(_verify_token),
):
    """Interactive clarify gate: Claude CLI asks back per gap, then scope fold.

    stage=questions → Claude CLI reads board (board_id/mode/focus_node_ids)
    and returns 0–4 personalized questions (or clear=true). Falls back to
    Ollama/static.
    stage=scope → deterministic fold of topic + answers into scope_brief.
    """
    return await run_clarify(body)
```

If the endpoint currently returns a raw model and FastAPI does not serialize it, wrap: `return (await run_clarify(body)).model_dump()`. Verify by re-running the test.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest test/integration/test_clarify_endpoint.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/topix/api/router/integration.py backend/test/integration/test_clarify_endpoint.py
git commit -m "feat(integration): forward board context through clarify endpoint"
```

---

## Task 7: Launcher — `doClarifyQuestions` clear branch + payload

**Files:**
- Modify: `webui/public/launcher.html` (`doClarifyQuestions`)
- Test: manual (vanilla JS, no harness) — steps below.

**Interfaces:**
- Consumes: `currentBoardId`/`boardId` (already in launcher scope), `MODE` from current step.
- Produces: sends `board_id`/`mode`/`focus_node_ids`; handles `clear:true` (skip questions, show rationale + "Chạy Explore" button).

- [ ] **Step 1: Manual failing check**

Open `webui/public/launcher.html` via the running app; enter a very detailed brief (goals+KPI+scope). Click "Làm rõ bài toán". Current behavior: always shows generic questions (no `clear` path, no `board_id` sent).

- [ ] **Step 2: Implement**

In `doClarifyQuestions()`, change the `fetch` body and the response handling. Replace the `body: JSON.stringify({...})` and the post-`const d = await r.json()` block:

```javascript
      body: JSON.stringify({
        topic: composed.slice(0, 2000),
        language: lastLanguage,
        stage: "questions",
        board_id: boardId || null,
        mode: lastResearchMode || "explore",
        focus_node_ids: lastFocusNodeIds || [],
      }),
```

And the response block (replace the `const questions = ...` through `showClarifyPhase("questions")`):

```javascript
      const d = await r.json()
      const clear = d.clear === true || d.data?.clear === true
      const rationale = d.rationale || d.data?.rationale || ""
      const questions = d.questions || d.data?.questions || []
      const model = d.model || d.data?.model || "—"

      if (clear) {
        // Agent says brief is already clear — skip questions, offer Explore.
        renderQuestions([])
        const root = document.getElementById("questionsList")
        root.innerHTML = `
          <div class="q-card" style="border-color: var(--accent, #6366f1);">
            <div class="q-title">✅ Agent thấy đề bài đã rõ</div>
            ${rationale ? `<div class="q-why">${rationale}</div>` : ""}
            <div class="q-why">Bạn có thể chạy Explore luôn, hoặc nhấn “Bỏ qua clarify” để tự chỉnh.</div>
          </div>
        `
        setStatus("clarifyStatus", "done", `✅ Đã rõ (model: ${model})`)
        showClarifyPhase("questions")
      } else {
        if (!questions.length) throw new Error("Không nhận được câu hỏi")
        renderQuestions(questions)
        setStatus("clarifyStatus", "done", `✅ ${questions.length} câu hỏi (model: ${model})`)
        showClarifyPhase("questions")
      }
```

Ensure `lastResearchMode` and `lastFocusNodeIds` exist — if not, declare near other `last*` vars (around `let lastClarifyQuestions = []`):

```javascript
  let lastResearchMode = "explore"
  let lastFocusNodeIds = []
```

(Set `lastResearchMode`/`lastFocusNodeIds` wherever the launcher chooses mode/focus for expand — at minimum they default to `"explore"`/`[]` which is correct for the explore flow.)

- [ ] **Step 3: Manual verify**

1. Start backend + serve `webui/public/launcher.html`. Create a board. Enter a detailed brief → "Làm rõ bài toán". Expect: either clear banner ("✅ Agent thấy đề bài đã rõ") OR grouped questions. Network tab: request body includes `board_id`, `mode`, `focus_node_ids`.
2. Enter a vague brief ("research ref") → expect grouped questions (not generic), each with an axis chip.

- [ ] **Step 4: Commit**

```bash
git add webui/public/launcher.html
git commit -m "feat(launcher): send board context + handle clear branch in clarify"
```

---

## Task 8: Launcher — `renderQuestions` group by axis

**Files:**
- Modify: `webui/public/launcher.html` (`renderQuestions`)
- Test: manual.

**Interfaces:**
- Consumes: `q.axis` on each question.
- Produces: questions rendered under section headers per axis.

- [ ] **Step 1: Implement**

Replace `renderQuestions` with an axis-grouping version:

```javascript
  const AXIS_LABEL = {
    storyline: { label: "🎬 Storyline / Cách kể", icon: "🎬" },
    tone: { label: "🎨 Tone & Mood", icon: "🎨" },
    craft: { label: "🛠️ Thủ pháp", icon: "🛠️" },
    industry: { label: "🏭 Ngành hàng", icon: "🏭" },
    scope: { label: "🎯 Phạm vi", icon: "🎯" },
    other: { label: "📌 Khác", icon: "📌" },
  }
  const AXIS_ORDER = ["industry", "storyline", "tone", "craft", "scope", "other"]

  function renderQuestions(questions) {
    /** Render clarify Q&A cards grouped by axis. */
    const root = document.getElementById("questionsList")
    root.innerHTML = ""
    lastClarifyQuestions = questions || []
    if (!lastClarifyQuestions.length) return

    const groups = {}
    lastClarifyQuestions.forEach((q) => {
      const ax = q.axis && AXIS_LABEL[q.axis] ? q.axis : "other"
      ;(groups[ax] = groups[ax] || []).push(q)
    })

    let counter = 0
    AXIS_ORDER.forEach((ax) => {
      const list = groups[ax]
      if (!list || !list.length) return
      const meta = AXIS_LABEL[ax]
      const header = document.createElement("div")
      header.className = "q-axis-header"
      header.style.cssText =
        "margin:0.75rem 0 0.35rem;font-size:0.8rem;font-weight:600;" +
        "text-transform:uppercase;letter-spacing:0.04em;color:var(--muted,#888);"
      header.textContent = meta.label
      root.appendChild(header)

      list.forEach((q) => {
        counter += 1
        const card = document.createElement("div")
        card.className = "q-card"
        const opts = (q.options || [])
          .map((o) =>
            `<button type="button" class="q-opt" data-qid="${q.id}" data-opt="${String(o).replace(/"/g, "&quot;")}">${o}</button>`)
          .join("")
        card.innerHTML = `
          <div class="q-title"><span class="q-axis-chip" style="font-size:0.65rem;padding:0 0.4rem;border-radius:999px;background:var(--accent-soft,rgba(99,102,241,.15));color:var(--accent,#6366f1);margin-right:0.4rem;">${meta.icon} ${ax}</span>${counter}. ${q.question || ""}</div>
          ${q.why ? `<div class="q-why">${q.why}</div>` : ""}
          ${opts ? `<div class="q-opts">${opts}</div>` : ""}
          <textarea id="ans_${q.id}" placeholder="${q.hint || "Câu trả lời của bạn…"}"></textarea>
        `
        root.appendChild(card)
      })
    })

    root.querySelectorAll(".q-opt").forEach((btn) => {
      btn.addEventListener("click", () => {
        const ta = document.getElementById("ans_" + btn.dataset.qid)
        if (ta) ta.value = btn.dataset.opt || btn.textContent
      })
    })
  }
```

- [ ] **Step 2: Manual verify**

Vague brief → questions appear under headers (Ngành hàng / Storyline / Tone & Mood / Thủ pháp / Phạm vi / Khác), each card with an axis chip. Numbering is continuous across groups.

- [ ] **Step 3: Commit**

```bash
git add webui/public/launcher.html
git commit -m "feat(launcher): group clarify questions by axis with section headers"
```

---

## Task 9: End-to-end manual verification checklist

**Files:** none (verification only).

- [ ] **Step 1: Fresh board explore**

Create new board. Brief: "ref sáng tạo cho BHNT gia đình VN". Click "Làm rõ bài toán".
- Expect: questions about industry/audience/refs (grouped), NOT "mục tiêu/đối tượng" generics.
- Network: request has `board_id`, `mode:"explore"`, `focus_node_ids:[]`.
- `model` field in status: `claude-cli` (if claude available) or `fallback`.

- [ ] **Step 2: Clear brief → clear:true**

Brief with explicit goals+KPI+scope+industry. Click "Làm rõ bài toán".
- Expect: "✅ Agent thấy đề bài đã rõ" banner + rationale; no questions.
- Click "Bỏ qua clarify → Explore thẳng" still works.

- [ ] **Step 3: Expand with focus**

Existing board with a cluster. Set `lastResearchMode="expand"` (via the launcher's expand entry). `focus_node_ids` = the cluster's node ids. Brief: "đào sâu cụm này".
- Expect: questions reference gaps in that specific cluster.

- [ ] **Step 4: Fallback path**

Stop the `claude` CLI (rename PATH). Click "Làm rõ".
- Expect: still returns questions (`model:"gemma"` or `"fallback"`), no crash.

- [ ] **Step 5: Scope → research**

Answer questions → "Chốt phạm vi". Expect deterministic scope_brief (`model:"fold"`) → "Explore" runs the existing research runner unchanged.

---

## Self-Review

**Spec coverage:**
- §4.1 schema additions → Task 1 ✓
- §4.1 `build_clarify_prompt` → Task 2 ✓
- §4.1 `run_clarify_questions_claude` → Task 3 ✓
- §4.1 rewrite `run_clarify` questions → Task 4 ✓
- §4.1 deterministic scope → Task 5 ✓
- §4.2 endpoint forward → Task 6 ✓
- §5.1 `doClarifyQuestions` clear + payload → Task 7 ✓
- §5.2 `renderQuestions` group by axis → Task 8 ✓
- §7 testing (unit + integration + manual e2e) → Tasks 1–6 + 9 ✓

**Placeholder scan:** none — all steps have real code/tests. Task 6 has a verify-step note on app import path (real uncertainty, addressed with instruction to verify).

**Type consistency:** `run_clarify_questions_claude(*, board_id, mode, instruction, focus_node_ids, language) -> ClarifyQuestionsOut` consistent across Tasks 3 & 4. `build_clarify_prompt(*, mode, instruction, focus_node_ids, language)` consistent across Tasks 2 & 3. `axis` literal set `storyline|tone|craft|industry|scope|other` consistent across schema, prompt, fallback helper, launcher.

No gaps. Plan ready.