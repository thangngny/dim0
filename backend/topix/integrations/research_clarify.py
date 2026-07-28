"""Interactive research clarification: questions → answers → locked scope brief.

Uses the same Ollama Cloud path as in-app agents (OLLAMA_API_KEY + OpenAI-compat
base URL). Output is structured JSON so the launcher can render a form, then feed
the confirmed scope into explore/reframe/expand as the research instruction.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
from typing import Any, Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ClarifyAnswer(BaseModel):
    """One user answer to a clarifying question."""

    id: str
    answer: str = Field(..., min_length=0, max_length=2000)


class ClarifyRequest(BaseModel):
    """Clarify pipeline request.

    - stage=questions: Claude CLI reads board + instruction, returns 0–4
      personalized questions (or clear=true) by real gaps — not generic.
    - stage=scope: deterministic fold of topic + answers into a scope brief.
    """

    topic: str = Field(..., min_length=1, max_length=2000)
    language: str = Field(default="vi", description="'vi' or 'en'")
    stage: Literal["questions", "scope"] = "questions"
    answers: list[ClarifyAnswer] = Field(default_factory=list)
    board_id: str | None = Field(default=None, description="Board for the Claude CLI pass to read.")
    mode: str | None = Field(default=None, description="explore|reframe|expand|critique.")
    focus_node_ids: list[str] = Field(default_factory=list)


class ClarifyQuestion(BaseModel):
    """A single clarifying question shown in the launcher form."""

    id: str
    question: str
    why: str = ""
    hint: str = ""
    options: list[str] = Field(default_factory=list)
    axis: str = Field(
        default="other",
        description="storyline|tone|craft|industry|scope|other — drives UI grouping.",
    )


class ClarifyQuestionsOut(BaseModel):
    """Response for stage=questions."""

    stage: Literal["questions"] = "questions"
    topic: str
    questions: list[ClarifyQuestion]
    model: str = ""
    clear: bool = Field(
        default=False,
        description="True when the agent says the brief is already clear (0 questions).",
    )
    rationale: str = Field(default="", description="Why clear/unclear — shown in the launcher.")


class ClarifyScopeOut(BaseModel):
    """Response for stage=scope — locked brief used as explore instruction."""

    stage: Literal["scope"] = "scope"
    topic: str
    problem_statement: str
    goals: list[str] = Field(default_factory=list)
    in_scope: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    research_axes: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    open_assumptions: list[str] = Field(default_factory=list)
    industry_traits: list[str] = Field(default_factory=list)
    scope_brief: str
    model: str = ""


def _ollama_chat_base_url() -> str:
    """Resolve OpenAI-compatible Ollama chat base URL."""
    explicit = os.getenv("OLLAMA_CHAT_BASE_URL")
    if explicit:
        return explicit.rstrip("/")
    base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    if base.endswith("/v1"):
        return base
    if os.getenv("OLLAMA_API_KEY") and base in (
        "http://localhost:11434",
        "http://127.0.0.1:11434",
    ):
        return "https://ollama.com/v1"
    return f"{base}/v1"


def _default_clarify_model() -> str:
    """Pick a fast Ollama model for interactive clarify (prefer low-reasoning)."""
    return (
        os.getenv("OLLAMA_CLARIFY_MODEL")
        or os.getenv("OLLAMA_CHAT_MODEL")
        or "gemma4:31b"
    )


def build_clarify_prompt(
    *,
    mode: str | None,
    instruction: str,
    focus_node_ids: list[str],
    language: str,
) -> str:
    """Build the Claude CLI clarify pass prompt.

    The agent reads the board via MCP (DIM0_DEFAULT_BOARD_ID is pinned by the
    caller), self-assesses clarity, and returns a JSON object with a clear flag
    plus 0–4 questions tagged by axis. Output is JSON only — no prose, no fence.
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
        f"STEP 2 — Assess: is INSTRUCTION + current board state clear enough to "
        f"start MODE={mode_s} WITHOUT guessing?\n\n"
        "OUTPUT RULES (mandatory):\n"
        "- Output ONLY a single JSON object. No prose, no markdown fence. "
        f"First character must be '{{'. All text in {out_lang}.\n"
        '- If clear enough: {"clear": true, "rationale": "<1 line why>"}.\n'
        '- If NOT clear: {"clear": false, "rationale": "<1 line>", '
        '"questions": [ {"id":"q1","question":"...","why":"...",'
        '"hint":"...","options":[...],"axis":"..."} ]}.\n'
        "- Ask ONLY about real gaps you cannot answer from the board+INSTRUCTION. "
        "1 to 4 questions max. Never ask generics already answerable.\n"
        "- Each question targets ONE concrete gap and has an `axis` in "
        "storyline|tone|craft|industry|scope|other.\n"
        "- options: 0–4 quick picks (user can still type freely).\n"
        "- For explore on an empty board: questions come from the brief "
        "(industry traits, audience, refs, scope) — NOT generic.\n"
        "- For expand/reframe/critique: questions must reference specific gaps "
        "in the existing board / focus cluster.\n"
    )


_CLARIFY_TIMEOUT = 90.0


def _repo_root() -> str:
    """Resolve monorepo root (parent of backend/); mirrors research_runner."""
    return os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..")
    )


async def run_clarify_questions_claude(
    *,
    board_id: str | None,
    mode: str | None,
    instruction: str,
    focus_node_ids: list[str],
    language: str,
) -> ClarifyQuestionsOut:
    """Spawn the Claude CLI clarify pass and parse its JSON output.

    The agent reads the board via MCP (DIM0_DEFAULT_BOARD_ID is pinned in the
    subprocess env). Raises on any failure (missing bin, no board, bad JSON,
    timeout) so the caller can fall back to Ollama/static questions.
    """
    import uuid

    claude_bin = shutil.which("claude")
    if not claude_bin:
        raise RuntimeError("claude CLI not found on PATH")
    if not board_id:
        raise RuntimeError("board_id required for the Claude CLI clarify pass")

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


def _extract_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object from model output (raw, fenced, or embedded)."""
    raw = (text or "").strip()
    if not raw:
        raise ValueError("Empty model response")
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.I)
    if fence:
        raw = fence.group(1).strip()
    candidates = [raw]
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        candidates.append(raw[start : end + 1])
    # sometimes model emits JSON after prose on the last line(s)
    for line in reversed(raw.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            candidates.append(line)
            break
    last_err: Exception | None = None
    for cand in candidates:
        try:
            data = json.loads(cand)
            if isinstance(data, dict):
                return data
        except Exception as exc:  # noqa: BLE001 — try next candidate
            last_err = exc
            continue
    raise ValueError(f"Could not parse JSON from model: {raw[:280]!r} ({last_err})")


async def _complete_json(system: str, user: str) -> tuple[dict[str, Any], str]:
    """Call Ollama Cloud via LiteLLM and return parsed JSON + model id.

    Retries once with a stricter JSON-only nudge if the first parse fails
    (common with reasoning models that wrap answers in prose).
    """
    import litellm

    litellm.drop_params = True
    model_name = _default_clarify_model()
    api_key = os.getenv("OLLAMA_API_KEY") or "ollama"
    base_url = _ollama_chat_base_url()
    litellm_model = f"openai/{model_name}"

    messages: list[dict[str, str]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    last_err: Exception | None = None
    for attempt in range(2):
        resp = await litellm.acompletion(
            model=litellm_model,
            messages=messages,
            api_base=base_url,
            api_key=api_key,
            max_tokens=4096,
            temperature=0.1,
        )
        msg = resp.choices[0].message
        content = (msg.content or "").strip()
        if not content or "{" not in content:
            # reasoning-only models: harvest JSON from reasoning fields
            for attr in ("reasoning_content", "reasoning"):
                extra = getattr(msg, attr, None)
                if isinstance(extra, str) and "{" in extra:
                    content = (content + "\n" + extra).strip() if content else extra
                    break
            psf = getattr(msg, "provider_specific_fields", None) or {}
            if isinstance(psf, dict):
                for key in ("reasoning", "reasoning_content"):
                    extra = psf.get(key)
                    if isinstance(extra, str) and "{" in extra:
                        content = (content + "\n" + extra).strip() if content else extra
        try:
            data = _extract_json_object(content)
            return data, model_name
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            logger.info("clarify JSON parse attempt %s failed: %s", attempt + 1, exc)
            messages.append({"role": "assistant", "content": content[:1500] or "(empty)"})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Invalid. Reply with ONLY a single valid JSON object. "
                        "First character must be '{'. No markdown, no prose."
                    ),
                }
            )
    raise ValueError(f"clarify JSON failed after retry: {last_err}")


def _questions_system(lang: str) -> str:
    """System prompt for generating clarifying questions."""
    if lang.lower().startswith("vi"):
        return (
            "Bạn là research PM cho brief sáng tạo / campaign / storytelling.\n"
            "Nhiệm vụ: hỏi làm rõ TRƯỚC khi research sâu.\n"
            "Trả về ĐÚNG một JSON object, không markdown, không giải thích ngoài JSON.\n"
            "Schema:\n"
            '{"questions":[{"id":"q1","question":"...","why":"vì sao cần hỏi",'
            '"hint":"gợi ý trả lời ngắn","options":["tuỳ chọn A","tuỳ chọn B"]}]}\n'
            "Quy tắc quan trọng:\n"
            "- 4 đến 6 câu hỏi (id q1..q6) — ngắn, rõ, tiếng Việt.\n"
            "- Nếu topic/brief đã đủ mục tiêu–kênh–thời gian–KPI: ĐỪNG hỏi lại generic.\n"
            "  Ưu tiên hỏi: (1) ngành hàng & đặc trưng ngành, (2) insight khách hàng ngành đó,\n"
            "  (3) reference mong muốn (storyline / tone&mood / thủ pháp), (4) brand constraints.\n"
            "- Nếu brief còn mỏng: hỏi mục tiêu, đối tượng, phạm vi in/out, tiêu chí thành công.\n"
            "- Luôn có Í ÍT NHẤT 1 câu về đặc trưng ngành hàng (category codes, pain, ritual mua…).\n"
            "- options: 0–4 gợi ý nhanh; user vẫn tự gõ được.\n"
            "- Không research, không trả lời topic — chỉ hỏi.\n"
            "- Output BẮT ĐẦU bằng '{' và KẾT THÚC bằng '}'."
        )
    return (
        "You are a research PM for creative / campaign / storytelling briefs.\n"
        "Return exactly one JSON object, no markdown.\n"
        'Schema: {"questions":[{"id":"q1","question":"...","why":"...","hint":"...","options":[]}]}\n'
        "Rules: 4–6 short questions. If the brief already has goals/channels/KPIs, do NOT "
        "re-ask generics — prioritize industry traits, category codes, storyline/tone/technique "
        "refs, brand constraints. Always include at least one industry-characteristics question. "
        "Do not answer the topic. Output MUST start with '{' and end with '}'."
    )


def _scope_system(lang: str) -> str:
    """System prompt for locking a research scope brief."""
    if lang.lower().startswith("vi"):
        return (
            "Bạn là research lead (brief sáng tạo / campaign). Từ topic + answers, CHỐT phạm vi.\n"
            "Trả về ĐÚNG một JSON object, không markdown.\n"
            "Schema:\n"
            "{\n"
            '  "problem_statement": "1–2 câu bài toán đã làm rõ",\n'
            '  "goals": ["..."],\n'
            '  "in_scope": ["..."],\n'
            '  "out_of_scope": ["..."],\n'
            '  "research_axes": ["Ref Storyline","Ref Tone & Mood","Ref Thủ pháp"],\n'
            '  "success_criteria": ["..."],\n'
            '  "open_assumptions": ["giả định còn lại nếu user bỏ trống"],\n'
            '  "industry_traits": ["đặc trưng ngành / category codes"],\n'
            '  "scope_brief": "đoạn 10–18 dòng khóa phạm vi cho agent explore"\n'
            "}\n"
            "Quy tắc:\n"
            "- research_axes MẶC ĐỊNH đúng 3 trục (có thể thêm 1 trục phụ nếu brief yêu cầu):\n"
            "  1) Ref Storyline (cách kể, tứ truyện, cốt truyện, arc)\n"
            "  2) Ref Tone & Mood (màu sắc, tạo hình, nhịp điệu, vibe)\n"
            "  3) Ref Thủ pháp (thủ pháp thể hiện, đồ họa, 3D, motion…)\n"
            "- scope_brief phải có các block rõ ràng bằng tiếng Việt:\n"
            "  INDUSTRY / IN_SCOPE / OUT_OF_SCOPE / AXES (3 trục trên) / SUCCESS / REFERENCES (link nếu có).\n"
            "- Không bịa số liệu. Thiếu thông tin → assumption rõ.\n"
            "- Không lan man: chỉ ref phục vụ đề bài, không dump list generic."
        )
    return (
        "You are a research lead for creative/campaign briefs. From topic + answers, lock scope.\n"
        "Return one JSON object only.\n"
        "Schema: problem_statement, goals[], in_scope[], out_of_scope[], research_axes[], "
        "success_criteria[], open_assumptions[], industry_traits[], scope_brief.\n"
        "research_axes default to exactly three: Ref Storyline, Ref Tone & Mood, Ref Technique. "
        "scope_brief must include INDUSTRY / IN_SCOPE / OUT_OF_SCOPE / AXES / SUCCESS / REFERENCES."
    )


def _fallback_questions(topic: str, lang: str) -> list[ClarifyQuestion]:
    """Static questions if the LLM is unavailable."""
    if lang.lower().startswith("vi"):
        return [
            ClarifyQuestion(
                id="q1",
                question="Ngành hàng / category cụ thể là gì? Đặc trưng ngành (pain, ritual mua, language) nổi bật?",
                why="Ref phải bám category codes, không generic",
                hint="vd. BHNT gia đình VN, F&B premium, edu-tech…",
                options=["Bảo hiểm", "F&B", "Ngân hàng", "Khác — mô tả thêm"],
            ),
            ClarifyQuestion(
                id="q2",
                question="Đối tượng & insight cảm xúc chính cần kể là gì?",
                why="Neo storyline và tone",
                hint="vd. bố mẹ 25–35 chuẩn bị hành trang cho con…",
            ),
            ClarifyQuestion(
                id="q3",
                question="Ưu tiên ref theo trục nào mạnh hơn?",
                why="Cân trọng số workstream",
                options=[
                    "Storyline (cách kể / tứ truyện)",
                    "Tone & Mood (màu, hình, nhịp)",
                    "Thủ pháp (đồ họa / 3D / motion)",
                    "Cân bằng cả 3",
                ],
            ),
            ClarifyQuestion(
                id="q4",
                question="Có brand / competitor / campaign reference cụ thể (tên hoặc link) không?",
                why="Neo evidence thật, tránh bịa",
                hint="dán link hoặc tên campaign…",
            ),
            ClarifyQuestion(
                id="q5",
                question="Cái gì NẰM NGOÀI phạm vi (không cần đào)?",
                why="Tránh research lan man",
                hint="vd. không pricing, không B2B, không thị trường quốc tế…",
            ),
            ClarifyQuestion(
                id="q6",
                question=f"Với “{topic[:80]}”, success của board research trông như thế nào?",
                why="Định nghĩa deliverable gọn",
                options=[
                    "3 cụm ref + decision",
                    "Moodboard hướng dẫn",
                    "Insight + recommendation",
                ],
            ),
        ]
    return [
        ClarifyQuestion(
            id="q1",
            question="What is the primary goal of this research?",
            why="Sets depth and deliverable",
            options=["Market understanding", "Competitor compare", "Framework", "Campaign brief"],
        ),
        ClarifyQuestion(
            id="q2",
            question="Which market / region / industry should we focus on?",
            why="Narrows context",
        ),
        ClarifyQuestion(
            id="q3",
            question="What time window matters?",
            options=["Last 2 years", "Last 5 years", "No limit"],
        ),
        ClarifyQuestion(
            id="q4",
            question="What is explicitly out of scope?",
            why="Prevents sprawl",
        ),
        ClarifyQuestion(
            id="q5",
            question="What does a successful research output look like?",
            options=["Framework + examples", "Comparison table", "Insights + recommendations"],
        ),
    ]


def _fallback_scope(topic: str, answers: list[ClarifyAnswer], lang: str) -> ClarifyScopeOut:
    """Build a usable scope brief without the LLM."""
    lines = [f"Q: {a.id} → {a.answer.strip()}" for a in answers if a.answer.strip()]
    joined = "\n".join(lines) or "(no answers)"
    axes = [
        "Ref Storyline (cách kể / tứ truyện / cốt truyện / arc)",
        "Ref Tone & Mood (màu sắc / tạo hình / nhịp điệu / vibe)",
        "Ref Thủ pháp (đồ họa / 3D / motion / thủ pháp thể hiện)",
    ]
    if lang.lower().startswith("vi"):
        brief = (
            f"SCOPE LOCKED\n"
            f"TOPIC: {topic}\n"
            f"USER ANSWERS:\n{joined}\n\n"
            f"INDUSTRY: bám đặc trưng ngành trong answers (nếu thiếu → assumption + Unknown).\n"
            f"IN_SCOPE: chỉ những gì user mô tả + 3 trục ref dưới.\n"
            f"OUT_OF_SCOPE: nhánh generic không liên quan ngành / answers.\n"
            f"AXES (bắt buộc — mỗi axis = 1 Workstream gọn):\n"
            f"  1) {axes[0]}\n"
            f"  2) {axes[1]}\n"
            f"  3) {axes[2]}\n"
            f"SUCCESS: graph gọn — 1 Question + 3 Workstream + Source/Finding bám axis + "
            f"Decision + Summary; không dump list bừa.\n"
            f"LANGUAGE: vi"
        )
        return ClarifyScopeOut(
            topic=topic,
            problem_statement=f"Làm rõ và research ref sáng tạo trong phạm vi: {topic}",
            goals=["Ref Storyline / Tone&Mood / Thủ pháp bám ngành"],
            in_scope=[topic, "3 trục ref sáng tạo"],
            out_of_scope=["Chủ đề ngoài answers", "Insight generic không bám ngành"],
            research_axes=axes,
            success_criteria=["Graph 3 cụm rõ", "Evidence có citation", "Decision gọn"],
            open_assumptions=["Answers thiếu → giữ hẹp, ghi Unknown"],
            industry_traits=[],
            scope_brief=brief,
            model="fallback",
        )
    brief = (
        f"SCOPE LOCKED\nTOPIC: {topic}\nANSWERS:\n{joined}\n"
        f"AXES: (1) Storyline (2) Tone&Mood (3) Technique. "
        f"Stay inside answers; neat 3-workstream graph; evidence-backed."
    )
    return ClarifyScopeOut(
        topic=topic,
        problem_statement=f"Research creative refs within clarified scope: {topic}",
        goals=["Storyline / Tone&Mood / Technique refs for the brief"],
        in_scope=[topic, "three creative ref axes"],
        out_of_scope=["Anything outside answers"],
        research_axes=[
            "Ref Storyline",
            "Ref Tone & Mood",
            "Ref Technique",
        ],
        success_criteria=["Neat 3-cluster graph", "Evidence present"],
        open_assumptions=["Missing answers → stay narrow"],
        industry_traits=[],
        scope_brief=brief,
        model="fallback",
    )


def _infer_axis_for_fallback(qtext: str) -> str:
    """Best-effort axis tag for Ollama/static-fallback questions (VN/EN keywords)."""
    t = (qtext or "").lower()
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


async def run_clarify(body: ClarifyRequest) -> ClarifyQuestionsOut | ClarifyScopeOut:
    """Run the questions or scope stage; falls back to templates on failure."""
    topic = body.topic.strip()
    lang = body.language or "vi"

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
                    id=qid,
                    question=qtext,
                    why=str(item.get("why") or ""),
                    hint=str(item.get("hint") or ""),
                    options=[str(o) for o in opts[:4] if str(o).strip()],
                    axis=str(item.get("axis") or _infer_axis_for_fallback(qtext)),
                ))
            if len(questions) < 1:
                raise ValueError("no usable questions from model")
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

    # stage == scope — deterministic fold (no LLM). The locked brief is built
    # purely from topic + answers via _fallback_scope, which already produces
    # a usable INDUSTRY/IN_SCOPE/OUT_OF_SCOPE/AXES/SUCCESS structure.
    return _fallback_scope(topic, body.answers, lang).model_copy(update={"model": "fold"})
