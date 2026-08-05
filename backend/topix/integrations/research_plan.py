"""Research plan gate: show the user the intended workstreams + search strategy
before the full research run executes (approve-before-run, SP2).

Reuses the clarify pipeline's LiteLLM JSON-completion helper so the plan
call uses the same Ollama Cloud path. Output is structured JSON the
launcher renders as an approvable plan; the user clicks "Approve & run"
to fire the existing research SSE.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from topix.integrations.research_clarify import _complete_json

logger = logging.getLogger(__name__)


class PlanRequest(BaseModel):
    """Plan gate request — runs after scope is locked, before execution."""

    topic: str = Field(..., min_length=1, max_length=2000)
    scope_brief: str = Field(default="", description="Locked scope from the clarify stage.")
    board_id: str | None = Field(default=None)
    mode: str = Field(default="explore")
    language: str = Field(default="vi")


class PlanWorkstream(BaseModel):
    """One planned research workstream."""

    id: str
    title: str
    axis: str = Field(default="", description="storyline|tone|craft|industry|…")
    search_strategy: str = Field(default="")
    intended_sources: list[str] = Field(default_factory=list)


class PlanOut(BaseModel):
    """Structured plan shown to the user for approval."""

    mode: str
    topic: str
    summary: str = Field(default="", description="1–2 line restatement of what the run will produce.")
    workstreams: list[PlanWorkstream] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    model: str = ""


def _plan_system(lang: str) -> str:
    vi = lang.lower().startswith("vi")
    out_lang = "Tiếng Việt" if vi else "English"
    return (
        f"You are the Dim0 research lead. A scope is locked; now produce the EXECUTION PLAN "
        f"the user will approve BEFORE any research runs. All text in {out_lang}.\n"
        "Output ONLY one JSON object — no prose, no markdown fence, first char '{'.\n"
        "Schema:\n"
        '{"mode":"...","topic":"...","summary":"<1-2 line what this run produces>",'
        '"workstreams":[{"id":"ws-1","title":"...","axis":"...","search_strategy":"<1 line>",'
        '"intended_sources":["..."]}],"risks":["<optional gap/risk>"]}\n'
        "Rules:\n"
        "- For a creative-ref brief: exactly 3 workstreams unless the scope axes say otherwise "
        "(Storyline / Tone&Mood / Technique).\n"
        "- Each workstream: a concrete search strategy (terms + source types) the user can judge, "
        "not a vague 'search for X'.\n"
        "- intended_sources: the KIND of source (e.g. 'Behance case studies', 'YouTube ads 2023+'), "
        "NOT invented URLs.\n"
        "- risks: 0–2 honest gaps (missing market, thin niche sources, etc.).\n"
    )


def _plan_user(req: PlanRequest) -> str:
    return (
        f"MODE={req.mode}\n"
        f"TOPIC:\n{req.topic.strip()[:2000]}\n\n"
        f"LOCKED SCOPE:\n{(req.scope_brief or '(none)').strip()[:4000]}\n\n"
        "Produce the plan JSON now."
    )


async def run_plan(body: PlanRequest) -> PlanOut:
    """Generate an approvable plan via one LiteLLM JSON call."""
    system = _plan_system(body.language)
    user = _plan_user(body)
    try:
        data, model = await _complete_json(system, user)
    except Exception as exc:  # noqa: BLE001
        logger.warning("research plan LLM call failed: %s", exc)
        # Degraded plan so the user can still approve + run.
        return PlanOut(
            mode=body.mode,
            topic=body.topic,
            summary="(plan unavailable — approve to run anyway)",
            workstreams=[],
            risks=["Plan generation failed; proceeding will run without a preview."],
            model="",
        )
    workstreams = [
        PlanWorkstream(
            id=str(ws.get("id") or f"ws-{i+1}"),
            title=str(ws.get("title") or "").strip(),
            axis=str(ws.get("axis") or "").strip(),
            search_strategy=str(ws.get("search_strategy") or "").strip(),
            intended_sources=[str(s) for s in (ws.get("intended_sources") or [])][:6],
        )
        for i, ws in enumerate(data.get("workstreams") or [])
    ]
    return PlanOut(
        mode=str(data.get("mode") or body.mode),
        topic=str(data.get("topic") or body.topic),
        summary=str(data.get("summary") or "").strip(),
        workstreams=workstreams[:5],
        risks=[str(r) for r in (data.get("risks") or [])][:3],
        model=model,
    )
