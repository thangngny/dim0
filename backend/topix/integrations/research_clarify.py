"""Interactive research clarification: questions → answers → locked scope brief.

Uses the same Ollama Cloud path as in-app agents (OLLAMA_API_KEY + OpenAI-compat
base URL). Output is structured JSON so the launcher can render a form, then feed
the confirmed scope into explore/reframe/expand as the research instruction.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ClarifyAnswer(BaseModel):
    """One user answer to a clarifying question."""

    id: str
    answer: str = Field(..., min_length=0, max_length=2000)


class ClarifyRequest(BaseModel):
    """Clarify pipeline request.

    - stage=questions: generate 4–7 clarifying questions for the topic
    - stage=scope: fold topic + answers into a locked research scope brief
    """

    topic: str = Field(..., min_length=1, max_length=2000)
    language: str = Field(default="vi", description="'vi' or 'en'")
    stage: Literal["questions", "scope"] = "questions"
    answers: list[ClarifyAnswer] = Field(default_factory=list)


class ClarifyQuestion(BaseModel):
    """A single clarifying question shown in the launcher form."""

    id: str
    question: str
    why: str = ""
    hint: str = ""
    options: list[str] = Field(default_factory=list)


class ClarifyQuestionsOut(BaseModel):
    """Response for stage=questions."""

    stage: Literal["questions"] = "questions"
    topic: str
    questions: list[ClarifyQuestion]
    model: str = ""


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
            "Bạn là research PM. Nhiệm vụ: hỏi làm rõ bài toán nghiên cứu trước khi đào sâu.\n"
            "Trả về ĐÚNG một JSON object, không markdown, không giải thích ngoài JSON.\n"
            "Schema:\n"
            '{"questions":[{"id":"q1","question":"...","why":"vì sao cần hỏi",'
            '"hint":"gợi ý trả lời ngắn","options":["tuỳ chọn A","tuỳ chọn B"]}]}\n'
            "Quy tắc:\n"
            "- 5 đến 7 câu hỏi (id q1..q7).\n"
            "- Ưu tiên: mục tiêu, đối tượng/thị trường, thời gian, tiêu chí thành công, "
            "phạm vi in/out, góc nhìn (competitor/campaign/framework…), ràng buộc (ngôn ngữ, ngành).\n"
            "- options: 0–4 gợi ý nhanh; user vẫn có thể tự gõ.\n"
            "- Câu hỏi ngắn, rõ, tiếng Việt.\n"
            "- Không research, không trả lời topic — chỉ hỏi.\n"
            "- Output BẮT ĐẦU bằng ký tự '{' và KẾT THÚC bằng '}'. Không text trước/sau."
        )
    return (
        "You are a research PM. Generate clarifying questions before deep research.\n"
        "Return exactly one JSON object, no markdown.\n"
        'Schema: {"questions":[{"id":"q1","question":"...","why":"...","hint":"...","options":[]}]}\n'
        "Rules: 5–7 questions covering goals, market, timeframe, success criteria, "
        "in/out scope, angle, constraints. Short English. Do not answer the topic.\n"
        "Output MUST start with '{' and end with '}'."
    )


def _scope_system(lang: str) -> str:
    """System prompt for locking a research scope brief."""
    if lang.lower().startswith("vi"):
        return (
            "Bạn là research lead. Từ topic + câu trả lời user, CHỐT phạm vi nghiên cứu.\n"
            "Trả về ĐÚNG một JSON object, không markdown.\n"
            "Schema:\n"
            "{\n"
            '  "problem_statement": "1–2 câu bài toán đã làm rõ",\n'
            '  "goals": ["..."],\n'
            '  "in_scope": ["..."],\n'
            '  "out_of_scope": ["..."],\n'
            '  "research_axes": ["trục phân tích 1","trục 2"],\n'
            '  "success_criteria": ["..."],\n'
            '  "open_assumptions": ["giả định còn lại nếu user bỏ trống"],\n'
            '  "scope_brief": "đoạn 8–15 dòng: brief khóa phạm vi để agent explore/research tuân theo"\n'
            "}\n"
            "Quy tắc:\n"
            "- scope_brief phải actionable, bằng tiếng Việt, có IN_SCOPE / OUT_OF_SCOPE / AXES / SUCCESS.\n"
            "- Không bịa số liệu. Nếu thiếu thông tin, ghi assumption rõ ràng.\n"
            "- research_axes: 3–6 trục để explore (workstreams)."
        )
    return (
        "You are a research lead. From topic + answers, lock research scope.\n"
        "Return one JSON object only.\n"
        "Schema: problem_statement, goals[], in_scope[], out_of_scope[], "
        "research_axes[], success_criteria[], open_assumptions[], scope_brief.\n"
        "scope_brief must be an actionable English brief agents will obey."
    )


def _fallback_questions(topic: str, lang: str) -> list[ClarifyQuestion]:
    """Static questions if the LLM is unavailable."""
    if lang.lower().startswith("vi"):
        return [
            ClarifyQuestion(
                id="q1",
                question="Mục tiêu chính của nghiên cứu này là gì?",
                why="Quyết định độ sâu và deliverable",
                hint="vd. chiến lược content, so sánh competitor, brief campaign…",
                options=["Hiểu market", "So sánh brand", "Lên framework", "Brief campaign"],
            ),
            ClarifyQuestion(
                id="q2",
                question="Thị trường / khu vực / ngành nào cần tập trung?",
                why="Thu hẹp địa bàn và context",
                hint="vd. Việt Nam, F&B, bảo hiểm toàn cầu…",
                options=["Việt Nam", "Đông Nam Á", "Toàn cầu", "Một ngành cụ thể"],
            ),
            ClarifyQuestion(
                id="q3",
                question="Khung thời gian bạn quan tâm?",
                why="Ưu tiên evidence gần đây vs lịch sử",
                options=["2 năm gần nhất", "5 năm", "Không giới hạn"],
            ),
            ClarifyQuestion(
                id="q4",
                question="Cái gì NẰM NGOÀI phạm vi (không cần đào)?",
                why="Tránh research lan man",
                hint="vd. không cần pricing, không cần B2B…",
            ),
            ClarifyQuestion(
                id="q5",
                question="Thành công của bản research này trông như thế nào?",
                why="Định nghĩa output hữu dụng",
                options=[
                    "Framework + ví dụ",
                    "Bảng so sánh brand",
                    "Insight + recommendation",
                ],
            ),
            ClarifyQuestion(
                id="q6",
                question=f"Với topic “{topic[:80]}”, góc nhìn ưu tiên là gì?",
                why="Chọn trục taxonomy",
                options=["Theo brand", "Theo storytelling mode", "Theo funnel", "Theo insight người dùng"],
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
    if lang.lower().startswith("vi"):
        brief = (
            f"SCOPE LOCKED\n"
            f"TOPIC: {topic}\n"
            f"USER ANSWERS:\n{joined}\n\n"
            f"IN_SCOPE: chỉ những gì user mô tả ở trên.\n"
            f"OUT_OF_SCOPE: mọi nhánh không liên quan answers.\n"
            f"AXES: suy ra 3–5 workstream bám answers; không lan man.\n"
            f"SUCCESS: graph research có evidence + finding + summary bám scope.\n"
            f"LANGUAGE: vi"
        )
        return ClarifyScopeOut(
            topic=topic,
            problem_statement=f"Làm rõ và nghiên cứu trong phạm vi: {topic}",
            goals=["Trả lời đúng mục tiêu user đã nêu"],
            in_scope=[topic],
            out_of_scope=["Chủ đề ngoài answers"],
            research_axes=["Bối cảnh", "Phân tích chính", "Evidence", "Hàm ý"],
            success_criteria=["Graph bám scope", "Có evidence"],
            open_assumptions=["Answers thiếu → giữ hẹp, ghi Unknown"],
            scope_brief=brief,
            model="fallback",
        )
    brief = (
        f"SCOPE LOCKED\nTOPIC: {topic}\nANSWERS:\n{joined}\n"
        f"Stay inside answers; 3–5 axes; evidence-backed findings."
    )
    return ClarifyScopeOut(
        topic=topic,
        problem_statement=f"Research within clarified scope: {topic}",
        goals=["Answer the user's stated goals"],
        in_scope=[topic],
        out_of_scope=["Anything outside answers"],
        research_axes=["Context", "Core analysis", "Evidence", "Implications"],
        success_criteria=["Scope-faithful graph", "Evidence present"],
        open_assumptions=["Missing answers → stay narrow"],
        scope_brief=brief,
        model="fallback",
    )


async def run_clarify(body: ClarifyRequest) -> ClarifyQuestionsOut | ClarifyScopeOut:
    """Run questions or scope stage; falls back to templates if LLM fails."""
    topic = body.topic.strip()
    lang = body.language or "vi"

    if body.stage == "questions":
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
                questions.append(
                    ClarifyQuestion(
                        id=qid,
                        question=qtext,
                        why=str(item.get("why") or ""),
                        hint=str(item.get("hint") or ""),
                        options=[str(o) for o in opts[:4] if str(o).strip()],
                    )
                )
            if len(questions) < 3:
                raise ValueError("too few questions from model")
            return ClarifyQuestionsOut(topic=topic, questions=questions, model=model)
        except Exception as exc:
            logger.warning("clarify questions LLM failed, using fallback: %s", exc)
            return ClarifyQuestionsOut(
                topic=topic,
                questions=_fallback_questions(topic, lang),
                model="fallback",
            )

    # stage == scope
    try:
        answers_blob = "\n".join(
            f"- {a.id}: {a.answer.strip() or '(empty)'}" for a in body.answers
        )
        data, model = await _complete_json(
            _scope_system(lang),
            f"TOPIC:\n{topic}\n\nANSWERS:\n{answers_blob}\n\nProduce locked scope JSON now.",
        )
        scope_brief = str(data.get("scope_brief") or "").strip()
        if not scope_brief:
            raise ValueError("missing scope_brief")

        def _str_list(key: str) -> list[str]:
            raw = data.get(key) or []
            if not isinstance(raw, list):
                return []
            return [str(x).strip() for x in raw if str(x).strip()][:12]

        return ClarifyScopeOut(
            topic=topic,
            problem_statement=str(data.get("problem_statement") or topic).strip(),
            goals=_str_list("goals"),
            in_scope=_str_list("in_scope"),
            out_of_scope=_str_list("out_of_scope"),
            research_axes=_str_list("research_axes"),
            success_criteria=_str_list("success_criteria"),
            open_assumptions=_str_list("open_assumptions"),
            scope_brief=scope_brief[:4000],
            model=model,
        )
    except Exception as exc:
        logger.warning("clarify scope LLM failed, using fallback: %s", exc)
        return _fallback_scope(topic, body.answers, lang)
