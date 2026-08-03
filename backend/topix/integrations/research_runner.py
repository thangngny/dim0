"""Multi-mode research runner: explore / reframe / expand / critique via Claude CLI + Dim0 MCP.

Board is persistent research memory. Each mode writes delta graph ops (not full re-gen),
except explore on a fresh board which may upsert a structured first graph.

Pipeline:
  1) optional web evidence briefing (Linkup/Tavily/…)
  2) mode-specific prompt + GraphWriter rules
  3) Claude CLI (--effort) with Dim0 MCP
  4) expand scope registration for server-side write guards
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
import uuid
from collections.abc import AsyncIterator
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ResearchMode(str, Enum):
    """Research controller modes for iterative board research."""

    EXPLORE = "explore"
    REFRAME = "reframe"
    EXPAND = "expand"
    CRITIQUE = "critique"
    IMPROVE = "improve"


class ResearchBudget(BaseModel):
    """Soft limits passed into the agent prompt.

    If effort is omitted, mode defaults apply (explore/reframe→ultracode,
    expand→xhigh, critique→high).
    """

    max_new_nodes: int = Field(default=20, ge=1, le=80)
    effort: Literal["high", "xhigh", "ultracode"] | None = None


class ResearchRequest(BaseModel):
    """Request body for multi-mode board research."""

    mode: ResearchMode = ResearchMode.EXPLORE
    instruction: str = Field(..., min_length=1, max_length=4000)
    language: str = Field(default="vi", description="'vi' or 'en'")
    focus_node_ids: list[str] = Field(default_factory=list)
    session_id: str | None = None
    budget: ResearchBudget = Field(default_factory=ResearchBudget)
    use_web_evidence: bool = Field(
        default=True,
        description="Collect web evidence briefing before Claude runs (if API keys exist).",
    )


def _repo_root() -> str:
    """Resolve monorepo root (parent of backend/)."""
    return os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..")
    )


def default_effort_for_mode(mode: ResearchMode) -> str:
    """Map mode to Claude CLI effort level."""
    if mode == ResearchMode.EXPLORE:
        return "ultracode"
    if mode == ResearchMode.REFRAME:
        return "ultracode"
    if mode == ResearchMode.EXPAND:
        return "xhigh"
    if mode == ResearchMode.IMPROVE:
        return "xhigh"
    return "high"


# Grace window the runner waits after a `completed` event arrived before
# any node was written — gives the agent time to land its graph write
# before the runner terminates the process. Tuned for ultracode writes.
COMPLETED_ZERO_NODE_GRACE_S = 30.0

# Hard backstop: kill a research run that has run this long regardless of
# agent state, so a hung Claude subprocess (stuck on the LLM provider, a
# blocked tool call, or a grandchild holding the stdout pipe open) cannot
# hang the launcher in "running" forever. Generous vs. legit ultracode
# runs (~12 min); tune up only if a real run legitimately exceeds this.
RESEARCH_RUN_TIMEOUT_S = 1800.0

# After the Claude subprocess exits (returncode set), wait this long for
# the stdout reader to deliver any remaining lines + `stdout_done` before
# breaking the loop ourselves. Without this, a grandchild holding the
# stdout pipe open means `stdout_done` never arrives and the loop polls
# forever even though Claude is already gone.
PROC_EXIT_DRAIN_GRACE_S = 5.0


def completed_early_done_action(
    *,
    completed: bool,
    last_node_count: int,
    baseline: int,
    grace_started: float | None,
    now: float,
    mode: ResearchMode | None = None,
    grace_seconds: float = COMPLETED_ZERO_NODE_GRACE_S,
) -> tuple[Literal["done", "wait_start", "wait_continue", "grace_expire"] | None, float | None]:
    """Decide the runner's response to an MCP `completed` event.

    Returns ``(action, grace_started)`` where ``action`` is:
      - ``"done"``          — graph was written (nodes > baseline); finish now.
      - ``"wait_start"``     — completed fired with 0 nodes; begin grace window.
      - ``"wait_continue"``  — still in grace window; keep polling for writes.
      - ``"grace_expire"``   — grace elapsed with 0 nodes; finish with warning.
      - ``None``             — not completed; no action this tick.

    ``grace_started`` is the (possibly newly set) grace-window timestamp.
    Extracted as a pure function so the 0-node guard is unit-testable
    without driving the full SSE/subprocess loop.

    ``mode``: improve/reframe/critique mutate existing nodes rather than
    creating new ones, so node count never exceeds baseline. For those
    modes a `completed` event is treated as a real finish immediately —
    the count-based 0-node guard would otherwise false-positive them.
    """
    if not completed:
        return None, grace_started
    if mode in (ResearchMode.IMPROVE, ResearchMode.REFRAME, ResearchMode.CRITIQUE):
        return "done", grace_started
    if last_node_count > baseline:
        return "done", grace_started
    if grace_started is None:
        return "wait_start", now
    if now - grace_started >= grace_seconds:
        return "grace_expire", grace_started
    return "wait_continue", grace_started


def graph_writer_rules(*, board_id: str, max_new_nodes: int, session_id: str, phase: str) -> str:
    """Shared GraphWriter contract for all modes."""
    return (
        "GRAPHWRITER RULES (mandatory):\n"
        "1) Always dim0_get_board / dim0_list_nodes before writing when board may have content.\n"
        "2) Prefer dim0_upsert_research_graph for multi-node phase writes (client_ref + edges).\n"
        "3) Use dim0_update_node / dim0_delete_node for remap; do not duplicate taxonomy.\n"
        "4) Stamp every node content with Kind + Phase + optional Brand/Campaign/Citations.\n"
        f"   Phase must be `{phase}`. Session `{session_id}`.\n"
        "5) Source/Evidence nodes MUST include URL citations when WEB_EVIDENCE provides them.\n"
        "   Never invent URLs. Use Unknown/low confidence when unsupported.\n"
        "6) Finding = claim; Source/Evidence = support; keep them separate nodes.\n"
        "7) Edge relations only: investigates, derived_from, supports, contradicts, depends_on, "
        "blocks, produces, leads_to, supersedes, summarizes.\n"
        f"8) Respect MAX_NEW_NODES={max_new_nodes}. Call dim0_layout_nodes after batch create.\n"
        "9) LIVE UI PROGRESS (mandatory — users watch sub-agents on launcher + canvas):\n"
        "   Call dim0_emit_research_event often with clear labels. Preferred event_type:\n"
        "   planning | workstream_started | source_found | finding_added | cross_checking |\n"
        "   synthesizing | agent_started | agent_progress | agent_done | completed | failed.\n"
        "   Always pass session_id and board_id. Optionally pass agent_id, role, detail, query:\n"
        "   - role: lead | workstream | collector | critique | writer\n"
        "   - agent_id: stable slug (lead, ws-tv, ws-customer, critique, writer)\n"
        "   - query: what is being searched (short)\n"
        "   - detail: one-line status for the UI card\n"
        "   FIRST: before any web_search or node write, emit ONE `planning` event with\n"
        "   role=lead, label=\"Kế hoạch\", detail listing the intended workstreams + axes +\n"
        "   1-line search strategy each — so the user sees the plan BEFORE results land.\n"
        "   Emit agent_started when spinning a workstream/sub-agent; agent_progress while\n"
        "   searching; source_found / finding_added when evidence lands; agent_done when done.\n"
        "   On ANY workstream/step failure: emit `failed` with the reason in detail\n"
        "   (e.g. \"ws-tone: Tavily quota hit, falling back to fewer sources\") so the user\n"
        "   understands what went wrong instead of seeing a silent stop.\n"
        "10) Do NOT paste chain-of-thought onto the board — only final graph content.\n"
        "11) Expand scope: if MODE=expand, never update/delete nodes outside FOCUS_NODE_IDS "
        "(server will reject). New children are allowed under focus.\n"
        f"12) ALWAYS pass board_id=\"{board_id}\" on EVERY Dim0 MCP tool call "
        f"(get_board, upsert, create, update, delete, layout, research_events). "
        f"Never omit board_id. Never write to another board.\n"
        "13) `completed` is a WRITE-POSTCONDITION, not a planning signal. Emit "
        "event_type=completed ONLY AFTER: (a) dim0_upsert_research_graph or "
        "dim0_create_nodes has returned success, AND (b) dim0_list_nodes confirms "
        ">=1 node exists on this board. NEVER emit completed before writing — the "
        "runner watches `completed` to terminate the run, so emitting it with 0 "
        "nodes KILLS the process before any write lands and leaves the board empty. "
        "If you genuinely have nothing to write, emit `failed` with the reason in "
        "detail instead. Order every run: write graph -> verify via list_nodes -> "
        "then emit completed.\n"
        "14) PRESENTATION / GỌN GÀNG (anti-dump):\n"
        "   - titles ≤ 50 chars, one idea per node, body 2–5 lines max.\n"
        "   - hierarchy edges only: question→workstream→finding→source (and summary→ws).\n"
        "   - Do NOT create many free-floating notes; every node under a workstream cluster.\n"
        "   - Prefer ≤ 3 workstreams for creative-ref briefs (Storyline / Tone&Mood / Technique).\n"
        "   - Call dim0_layout_nodes after each batch (server hierarchical research layout).\n"
        "   - If content is long, put bullets in one finding — never spam 10 near-duplicate nodes.\n"
        "15) CITATION INTEGRITY (anti-hallucination — mandatory):\n"
        "   - NEVER invent a URL. A Source/Evidence node MUST carry a real URL from\n"
        "     WEB_EVIDENCE or user REFERENCES only. If you have no real URL, mark the\n"
        "     node `unknown`/low confidence and say so — do NOT fabricate a link.\n"
        "   - Before creating a Source node, call dim0_list_nodes; if a Source with the\n"
        "     SAME URL already exists, do NOT recreate it — reference the existing one\n"
        "     (the server also dedupes by URL, but avoid the wasted call).\n"
        "   - Prefer primary/official sources (brand site, official report, original paper,\n"
        "     court filing, stat bureau) over aggregators/blog spam. Rank: official > news\n"
        "     > trade press > forum/blog. State the source type in the node.\n"
        "   - Check the publication date. If the topic moves fast (AI, markets, policy) and\n"
        "     the source is >24 months old, prefer a newer one or add an `unknown`/stale\n"
        "     note. Stamp `year` in metadata when known.\n"
        "   - If two sources repeat the same claim, cite ONE (the more authoritative) and\n"
        "     note the duplicate in a single Finding — do not create parallel copies.\n"
        "   - Every Finding that makes a factual claim MUST link (edge) to ≥1 Source node.\n"
        "16) VIETNAMESE QUALITY (when language=vi — mandatory, this is a common complaint):\n"
        "   - Write natural, idiomatic Vietnamese — NOT machine-translated phrasing.\n"
        "     Avoid calques like 'làm cho nó', 'để mà', English word-order, or stiff\n"
        "     'S sẽ được V bởi O' passives. Prefer active, conversational-but-clear tone.\n"
        "   - Correct spelling + diacritics. Brand/product names keep original spelling.\n"
        "   - No boilerplate filler ('Trong thế giới ngày nay', 'Không thể phủ nhận').\n"
        "   - Keep terms consistent across nodes (one term per concept).\n"
        "   - When IMPROVE-ing a node: PRESERVE its good parts; refine, don't replace.\n"
        "17) MEMORY ACROSS ROUNDS (the board IS your long-term memory):\n"
        "   - ALWAYS dim0_get_board / dim0_list_nodes first. Read existing decisions,\n"
        "     rejected alternatives, and prior findings BEFORE proposing anything new.\n"
        "   - Never re-propose an option the board already rejected (check Contradiction /\n"
        "     Unknown / Decision nodes). If you must revisit it, say WHY the earlier\n"
        "     rejection no longer holds.\n"
        "   - Each pass builds on the previous one — do not act as if the board is empty.\n"
        "18) VIDEO REFERENCES (when INSTRUCTION asks for video/refs):\n"
        "   - Prefer DIRECT platform links as the Source URL: youtube.com/watch?v=…,\n"
        "     vimeo.com/…, pinterest.com/pin/…, behance.net/…, dribbble.com/…\n"
        "   - Do NOT hand back a generic search-result page; land on the actual asset.\n"
        "   - One Source node per video with title + platform + why-it-fits in the body.\n"
        "19) ANALYSIS DEPTH & COUNTERVIEW (don't just list findings):\n"
        "   - Proactively create ONE Contradiction or Unknown node when sources genuinely\n"
        "     disagree OR a key question stays unresolved — surface it, don't hide it.\n"
        "   - For a strategic/creative brief, add a Finding that states the opposing view\n"
        "     or the risk of the recommended direction (not only the case FOR it).\n"
        "   - Distinguish 'popular/common' from 'insightful/niche' — flag when a finding\n"
        "     is something most people already know vs a non-obvious angle worth keeping.\n"
        "20) KNOW WHEN TO STOP (anti-over-research):\n"
        "   - Stop collecting once each workstream has 1–3 real Sources + 1–2 Findings\n"
        "     grounded in them. More searches after that = diminishing returns + duplicate\n"
        "     evidence. Do NOT pad the board to hit MAX_NEW_NODES.\n"
        "   - If the first round already covers an axis well, skip further rounds for it.\n"
        "   - Prefer fewer, higher-quality sources over many shallow ones.\n"
    )


def build_research_prompt(
    *,
    board_id: str,
    mode: ResearchMode,
    instruction: str,
    language: str,
    session_id: str,
    focus_node_ids: list[str],
    max_new_nodes: int,
    evidence_briefing: str = "",
) -> str:
    """Build a mode-specific ultracode/MCP prompt for Claude CLI."""
    instruction_safe = instruction.strip()[:4000]
    focus = ", ".join(focus_node_ids[:20]) if focus_node_ids else ""
    lang_vi = language.lower().startswith("vi")
    phase = mode.value

    meta_block = (
        f"BOARD_ID={board_id}\n"
        f"MODE={mode.value}\n"
        f"SESSION_ID={session_id}\n"
        f"MAX_NEW_NODES={max_new_nodes}\n"
        f"FOCUS_NODE_IDS={focus or '(none)'}\n"
        f"INSTRUCTION:\n{instruction_safe}\n"
    )

    tools = (
        "Dim0 MCP tools: dim0_get_board, dim0_list_nodes, dim0_upsert_research_graph, "
        "dim0_create_nodes, dim0_update_node, dim0_delete_node, dim0_delete_edge, "
        "dim0_layout_nodes, dim0_emit_research_event.\n"
        "Node kinds: question, workstream, source, evidence, finding, hypothesis, "
        "contradiction, unknown, alternative, decision, summary, status, note.\n"
    )

    rules = graph_writer_rules(
        board_id=board_id,
        max_new_nodes=max_new_nodes,
        session_id=session_id,
        phase=phase,
    )
    evidence = evidence_briefing.strip() or "WEB_EVIDENCE: (none provided)"

    if mode == ResearchMode.EXPLORE:
        if lang_vi:
            task = (
                "MODE=explore — quét lần đầu THEO PHẠM VI ĐÃ CHỐT.\n"
                "Nếu INSTRUCTION chứa SCOPE / IN_SCOPE / OUT_OF_SCOPE / AXES: "
                "tuân thủ tuyệt đối — không lan man; workstream = research_axes.\n"
                "\n"
                "CẤU TRÚC GRAPH BẮT BUỘC (gọn, không nhả bừa):\n"
                "1 Question · đúng 3 Workstream (trừ khi AXES chỉ định khác):\n"
                "  WS1 = Ref Storyline (cách kể, tứ truyện, cốt truyện, arc)\n"
                "  WS2 = Ref Tone & Mood (màu, tạo hình, nhịp, vibe)\n"
                "  WS3 = Ref Thủ pháp (đồ họa, 3D, motion, thủ pháp thể hiện)\n"
                "Mỗi WS: 1–3 Source (URL thật từ WEB_EVIDENCE / REFERENCES) + 1–2 Finding.\n"
                "Toàn board: tối đa 1 Contradiction hoặc Unknown · 1 Decision · 1 Summary.\n"
                "Tổng node ≤ min(MAX_NEW_NODES, 22). Title ≤ 50 ký tự. Body 2–5 dòng.\n"
                "Không dump list generic; mỗi finding phải bám đặc trưng ngành trong INSTRUCTION.\n"
                "Nếu có REFERENCES / link user: ưu tiên fetch ý từ đó (không bịa URL).\n"
                "\n"
                "Orchestrate: plan → collect theo 3 WS song song → critique → write + layout.\n"
                "TRƯỚC mỗi WS: emit workstream_started (agent_id=ws-storyline|ws-tone|ws-craft).\n"
                "KHI search: agent_progress + query. KHI có nguồn: source_found. "
                "SAU critique: cross_checking. TRƯỚC write: synthesizing. CUỐI: completed.\n"
                f"idempotency_key='{session_id}-explore'.\n"
                "Tiếng Việt; label UI ngắn dễ hiểu."
            )
        else:
            task = (
                "MODE=explore — first pass RESPECTING LOCKED SCOPE in INSTRUCTION.\n"
                "If SCOPE / AXES present: obey strictly; workstreams = research axes.\n"
                "NEAT GRAPH (no dump): 1 Question + exactly 3 Workstreams unless AXES say otherwise:\n"
                "  WS1 Ref Storyline · WS2 Ref Tone&Mood · WS3 Ref Technique.\n"
                "Each WS: 1–3 real Sources + 1–2 Findings. At most 1 Contradiction/Unknown, "
                "1 Decision, 1 Summary. Cap nodes ≤ min(MAX_NEW_NODES, 22). "
                "Ground findings in industry traits; prefer user REFERENCES URLs.\n"
                "Emit live sub-agent events (agent_started/progress/source_found/completed).\n"
                f"idempotency_key='{session_id}-explore'. English progress."
            )
    elif mode == ResearchMode.REFRAME:
        if lang_vi:
            task = (
                "MODE=reframe — ĐỔI TRỤC taxonomy TOÀN BOARD (không board mới).\n"
                "1) get_board 2) map taxonomy cũ → mới trong INSTRUCTION "
                "3) workstream = axes mới 4) remap evidence "
                "5) merge/xóa workstream cũ trùng 6) update Summary/Decision "
                "7) delta only.\n"
                f"idempotency_key='{session_id}-reframe'. Tiếng Việt."
            )
        else:
            task = (
                "MODE=reframe — change whole-board taxonomy per INSTRUCTION; "
                "remap evidence; merge obsolete workstreams; delta only. "
                f"idempotency_key='{session_id}-reframe'."
            )
    elif mode == ResearchMode.EXPAND:
        focus_rule = (
            f"FOCUS only: {focus}. Never rewrite other branches."
            if focus
            else "Infer focus cluster from INSTRUCTION; keep edits local."
        )
        if lang_vi:
            task = (
                f"MODE=expand — đào sâu cụm/node.\n{focus_rule}\n"
                "Thêm Source/Evidence/Finding con; edge về focus; "
                "dùng WEB_EVIDENCE cho citation thật.\n"
                f"idempotency_key='{session_id}-expand'. Tiếng Việt."
            )
        else:
            task = (
                f"MODE=expand — deepen focus cluster.\n{focus_rule}\n"
                "Add child sources/findings with real citations from WEB_EVIDENCE. "
                f"idempotency_key='{session_id}-expand'."
            )
    elif mode == ResearchMode.IMPROVE:
        focus_rule = (
            f"Improve ONLY the focus node(s): {focus}. Never touch other nodes."
            if focus
            else "Pick the single node INSTRUCTION targets; touch only that one."
        )
        if lang_vi:
            task = (
                "MODE=improve — CẢI THIỆN một node hiện có, KHÔNG xóa-làm-mới.\n"
                f"{focus_rule}\n"
                "1) get_board / list_nodes để đọc node fOCUS + neighbours. "
                "2) Dùng dim0_update_node để viết lại content node đó: giữ kind, "
                "giữ edges, giữ cấu trúc bullet, chỉ nâng cấp theo INSTRUCTION "
                "(sửa văn phong, rõ hơn, thêm chi tiết, bỏ thừa). "
                "3) KHÔNG dim0_delete_node trừ khi INSTRUCTION yêu cầu rõ. "
                "4) Giữ nguyên citation/URL thật đã có; chỉ thêm khi có nguồn mới thật.\n"
                f"idempotency_key='{session_id}-improve'. Tiếng Việt tự nhiên."
            )
        else:
            task = (
                "MODE=improve — refine an EXISTING node, do NOT delete-and-rebuild.\n"
                f"{focus_rule}\n"
                "Read the focus node + neighbors, then dim0_update_node to rewrite its "
                "content: keep kind, edges, structure; only improve per INSTRUCTION. "
                "Do not dim0_delete_node unless INSTRUCTION explicitly asks. "
                "Keep real citations; only add new ones with real URLs.\n"
                f"idempotency_key='{session_id}-improve'."
            )
    else:  # critique
        if lang_vi:
            task = (
                "MODE=critique — audit graph: gap evidence, contradiction, "
                "claim không có source, taxonomy mơ hồ.\n"
                "Thêm/cập nhật Contradiction, Unknown, Finding về lỗ hổng; "
                "không rewrite taxonomy trừ khi INSTRUCTION yêu cầu.\n"
                "Gắn citation nếu WEB_EVIDENCE phủ nhận/ủng hộ claim.\n"
                f"idempotency_key='{session_id}-critique'. Tiếng Việt."
            )
        else:
            task = (
                "MODE=critique — audit gaps/contradictions/unsupported claims; "
                "add Unknown/Contradiction/Finding; use WEB_EVIDENCE. "
                f"idempotency_key='{session_id}-critique'."
            )

    return (
        f"ultracode\n\n"
        f"You are the Dim0 lead research agent (orchestrate sub-agents when useful).\n\n"
        f"{meta_block}\n"
        f"{tools}\n"
        f"{rules}\n"
        f"{evidence}\n\n"
        f"{task}\n"
    )


async def stream_research_claude(
    *,
    board_id: str,
    body: ResearchRequest,
) -> AsyncIterator[str]:
    """Yield SSE `data: {...}\\n\\n` lines while Claude CLI runs research."""
    from topix.integrations.evidence_collect import collect_evidence_briefing
    from topix.integrations.research_scope import begin_expand_scope, end_scope

    claude_bin = shutil.which("claude")
    session_id = body.session_id or str(uuid.uuid4())
    effort = body.budget.effort or default_effort_for_mode(body.mode)

    if not claude_bin:
        yield f"data: {json.dumps({'status': 'error', 'detail': 'claude CLI not found on PATH'})}\n\n"
        return

    from topix.integrations.research_progress import track_session as _track_early

    # Track session before evidence so early agent cards survive.
    _track_early(session_id, board_id, body.mode.value)

    yield (
        "data: "
        + json.dumps(
            {
                "status": "starting",
                "session_id": session_id,
                "mode": body.mode.value,
                "effort": effort,
            }
        )
        + "\n\n"
    )

    # Scope gate registration for focus-scoped modes (expand + improve).
    if body.mode in (ResearchMode.EXPAND, ResearchMode.IMPROVE):
        begin_expand_scope(
            board_id,
            session_id,
            body.focus_node_ids,
            max_new_nodes=body.budget.max_new_nodes,
        )
        yield (
            "data: "
            + json.dumps(
                {
                    "status": "running",
                    "message": f"Expand scope ON · focus={len(body.focus_node_ids)} node(s)",
                }
            )
            + "\n\n"
        )

    evidence_briefing = ""
    if body.use_web_evidence:
        yield (
            "data: "
            + json.dumps({"status": "running", "message": "Collecting web evidence…"})
            + "\n\n"
        )
        try:
            briefing = await collect_evidence_briefing(
                body.instruction,
                language=body.language,
            )
            evidence_briefing = briefing.get("briefing_text") or ""
            n_res = len(briefing.get("results") or [])
            eng = briefing.get("engines")
            yield (
                "data: "
                + json.dumps(
                    {
                        "status": "progress",
                        "text": (
                            f"WEB_EVIDENCE engines={eng} "
                            f"results={n_res} "
                            f"available={briefing.get('available')}"
                        ),
                    }
                )
                + "\n\n"
            )
            # Structured card so UI shows web-collector before Claude starts.
            from topix.integrations.research_progress import record_event as _rec_ev

            _rec_ev(
                session_id=session_id,
                board_id=board_id,
                event_type="agent_progress" if n_res else "agent_started",
                label="Web evidence collector",
                agent_id="web-evidence",
                role="collector",
                detail=f"engines={eng} · {n_res} results",
                query=(body.instruction or "")[:120],
            )
            yield (
                "data: "
                + json.dumps(
                    {
                        "status": "agent",
                        "session_id": session_id,
                        "board_id": board_id,
                        "event": {
                            "event_type": "agent_progress",
                            "label": "Web evidence collector",
                            "agent_id": "web-evidence",
                            "role": "collector",
                            "detail": f"engines={eng} · {n_res} results",
                            "query": (body.instruction or "")[:120],
                        },
                    }
                )
                + "\n\n"
            )
        except Exception as exc:
            logger.exception("evidence collect failed")
            evidence_briefing = f"WEB_EVIDENCE: error ({exc}); proceed with care."

    prompt = build_research_prompt(
        board_id=board_id,
        mode=body.mode,
        instruction=body.instruction,
        language=body.language,
        session_id=session_id,
        focus_node_ids=body.focus_node_ids,
        max_new_nodes=body.budget.max_new_nodes,
        evidence_briefing=evidence_briefing,
    )

    # Always pin MCP default board to this run (overwrite any stale env / .mcp.json).
    env = {**os.environ}
    env["DIM0_BASE_URL"] = env.get("DIM0_BASE_URL") or "http://localhost:8899"
    env["DIM0_DEFAULT_BOARD_ID"] = board_id
    if env.get("DIM0_INTEGRATION_TOKEN") is None and os.getenv("DIM0_INTEGRATION_TOKEN"):
        env["DIM0_INTEGRATION_TOKEN"] = os.getenv("DIM0_INTEGRATION_TOKEN", "")

    mcp_dir = _repo_root()
    mode_label = {
        ResearchMode.EXPLORE: "explore (quét rộng)",
        ResearchMode.REFRAME: "reframe (đổi taxonomy)",
        ResearchMode.EXPAND: "expand (đào sâu cụm)",
        ResearchMode.CRITIQUE: "critique (rà soát)",
        ResearchMode.IMPROVE: "improve (cải thiện node)",
    }.get(body.mode, body.mode.value)

    from topix.integrations.research_progress import (
        clear_session,
        get_progress,
        list_events_since,
        set_nodes_seen,
        snapshot_dict,
    )

    # Session already tracked at start; do not reset (keeps web-evidence cards).
    event_cursor = 0

    yield (
        "data: "
        + json.dumps(
            {
                "status": "running",
                "message": f"Claude [{effort}] · {mode_label}…",
                "session_id": session_id,
                "board_id": board_id,
            }
        )
        + "\n\n"
    )
    # Seed UI with any events already recorded (planning + web collector).
    seed_events, event_cursor = list_events_since(session_id, 0)
    for se in seed_events:
        yield (
            "data: "
            + json.dumps(
                {
                    "status": "agent",
                    "session_id": session_id,
                    "board_id": board_id,
                    "event": {
                        "id": se.id,
                        "event_type": se.event_type,
                        "label": se.label,
                        "agent_id": se.agent_id,
                        "role": se.role,
                        "detail": se.detail,
                        "query": se.query,
                        "ts": se.ts,
                    },
                }
            )
            + "\n\n"
        )
    prog0 = get_progress(session_id)
    if prog0 is not None:
        yield (
            "data: "
            + json.dumps(
                {
                    "status": "agents_snapshot",
                    "session_id": session_id,
                    "board_id": board_id,
                    "snapshot": snapshot_dict(prog0),
                }
            )
            + "\n\n"
        )

    async def _count_nodes() -> int:
        """Best-effort node count via integration API (local)."""
        try:
            import httpx

            base = env.get("DIM0_BASE_URL", "http://localhost:8899").rstrip("/")
            token = env.get("DIM0_INTEGRATION_TOKEN") or os.getenv("DIM0_INTEGRATION_TOKEN", "")
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(
                    f"{base}/integration/boards/{board_id}/nodes",
                    headers={"X-Integration-Token": token},
                )
                if r.status_code >= 400:
                    return -1
                data = r.json()
                return len(data.get("nodes") or [])
        except Exception:
            return -1

    try:
        proc = await asyncio.create_subprocess_exec(
            claude_bin,
            "--dangerously-skip-permissions",
            "--effort",
            effort,
            "-p",
            prompt,
            "--output-format",
            "text",
            cwd=mcp_dir,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert proc.stdout

        early_done = False
        last_node_count = -1
        stable_since: float | None = None
        completion_grace_started: float | None = None
        # Track subprocess exit + overall run deadline so a hung Claude
        # (or a grandchild holding the stdout pipe) cannot park the run in
        # "running" forever — see PROC_EXIT_DRAIN_GRACE_S / RESEARCH_RUN_TIMEOUT_S.
        proc_ended_at: float | None = None
        run_started_at = time.time()
        timed_out = False
        baseline = await _count_nodes()
        if baseline < 0:
            baseline = 0

        # Multiplex stdout + periodic board/progress polls so UI can finish
        # when the graph is written, without waiting for Claude to exit.
        async def _poll_loop(queue: asyncio.Queue) -> None:
            try:
                while True:
                    await asyncio.sleep(4.0)
                    await queue.put(("poll", None))
            except asyncio.CancelledError:
                return

        async def _stdout_loop(queue: asyncio.Queue) -> None:
            try:
                async for line in proc.stdout:
                    await queue.put(("stdout", line))
                await queue.put(("stdout_done", None))
            except asyncio.CancelledError:
                return

        q: asyncio.Queue = asyncio.Queue()
        poll_task = asyncio.create_task(_poll_loop(q))
        out_task = asyncio.create_task(_stdout_loop(q))

        try:
            while True:
                kind, payload = await q.get()
                if kind == "stdout":
                    text = payload.decode("utf-8", errors="replace").rstrip()
                    if text:
                        yield f"data: {json.dumps({'status': 'progress', 'text': text})}\n\n"
                elif kind == "stdout_done":
                    break
                elif kind == "poll":
                    # Flush new structured agent events to SSE (launcher timeline).
                    new_events, event_cursor = list_events_since(session_id, event_cursor)
                    for se in new_events:
                        yield (
                            "data: "
                            + json.dumps(
                                {
                                    "status": "agent",
                                    "session_id": session_id,
                                    "board_id": board_id,
                                    "event": {
                                        "id": se.id,
                                        "event_type": se.event_type,
                                        "label": se.label,
                                        "agent_id": se.agent_id,
                                        "role": se.role,
                                        "detail": se.detail,
                                        "query": se.query,
                                        "ts": se.ts,
                                    },
                                }
                            )
                            + "\n\n"
                        )
                    prog = get_progress(session_id)
                    if prog is not None and new_events:
                        yield (
                            "data: "
                            + json.dumps(
                                {
                                    "status": "agents_snapshot",
                                    "session_id": session_id,
                                    "board_id": board_id,
                                    "snapshot": snapshot_dict(prog),
                                }
                            )
                            + "\n\n"
                        )

                    count = await _count_nodes()
                    if count >= 0:
                        set_nodes_seen(session_id, count)
                        if count != last_node_count:
                            if count > last_node_count >= 0 or (last_node_count < 0 and count > baseline):
                                yield (
                                    "data: "
                                    + json.dumps(
                                        {
                                            "status": "progress",
                                            "text": f"BOARD_NODES={count} (baseline={baseline})",
                                        }
                                    )
                                    + "\n\n"
                                )
                            last_node_count = count
                            stable_since = time.time()
                        elif count > baseline and stable_since is None:
                            stable_since = time.time()

                    # Early done: MCP completed event — but only treat as done
                    # when the graph has actually been written. Agents sometimes
                    # emit `completed` before calling the write tool; breaking on
                    # that premature signal kills Claude before nodes land →
                    # 0-node board. Guard: require nodes > baseline, else grace.
                    if prog and prog.completed:
                        action, completion_grace_started = completed_early_done_action(
                            completed=True,
                            last_node_count=last_node_count,
                            baseline=baseline,
                            grace_started=completion_grace_started,
                            now=time.time(),
                            mode=body.mode,
                        )
                        if action == "done":
                            yield (
                                "data: "
                                + json.dumps(
                                    {
                                        "status": "progress",
                                        "text": f"Research completed event: {prog.last_label or 'ok'}",
                                    }
                                )
                                + "\n\n"
                            )
                            early_done = True
                            break
                        elif action == "wait_start":
                            yield (
                                "data: "
                                + json.dumps(
                                    {
                                        "status": "progress",
                                        "text": (
                                            "Completed event received but 0 nodes written — "
                                            "waiting for graph write…"
                                        ),
                                    }
                                )
                                + "\n\n"
                            )
                        elif action == "grace_expire":
                            yield (
                                "data: "
                                + json.dumps(
                                    {
                                        "status": "progress",
                                        "text": (
                                            "Completed event but 0 nodes after "
                                            f"{COMPLETED_ZERO_NODE_GRACE_S:.0f}s grace — "
                                            "finishing (research wrote nothing)."
                                        ),
                                    }
                                )
                                + "\n\n"
                            )
                            early_done = True
                            break

                    if prog and prog.failed:
                        yield (
                            "data: "
                            + json.dumps(
                                {
                                    "status": "error",
                                    "detail": prog.last_label or "research failed event",
                                }
                            )
                            + "\n\n"
                        )
                        early_done = True
                        break

                    # Early done: graph grew and stayed stable ≥12s
                    grew = count >= 0 and count >= baseline + 3
                    stable = stable_since is not None and (time.time() - stable_since) >= 12.0
                    if grew and stable and last_node_count == count:
                        yield (
                            "data: "
                            + json.dumps(
                                {
                                    "status": "progress",
                                    "text": (
                                        f"Graph stable at {count} nodes — finishing UI "
                                        "(Claude may still wind down)."
                                    ),
                                }
                            )
                            + "\n\n"
                        )
                        early_done = True
                        break

                # Hard backstop: a run exceeding RESEARCH_RUN_TIMEOUT_S is a
                # hung Claude (stuck provider call, blocked tool, or a grandchild
                # holding the stdout pipe). Terminate instead of polling forever.
                if (not timed_out) and (time.time() - run_started_at) > RESEARCH_RUN_TIMEOUT_S:
                    timed_out = True
                    yield (
                        "data: "
                        + json.dumps(
                            {
                                "status": "error",
                                "detail": (
                                    f"research timed out after "
                                    f"{RESEARCH_RUN_TIMEOUT_S:.0f}s — claude did not finish"
                                ),
                            }
                        )
                        + "\n\n"
                    )
                    early_done = True
                    break

                # Claude exited. `stdout_done` normally ends the loop, but a
                # grandchild can hold the stdout pipe open so it never arrives.
                # After a short drain grace, break ourselves so the run
                # terminates (and clear_session runs) instead of polling forever.
                if proc.returncode is not None and kind != "stdout":
                    if proc_ended_at is None:
                        proc_ended_at = time.time()
                    elif time.time() - proc_ended_at >= PROC_EXIT_DRAIN_GRACE_S:
                        yield (
                            "data: "
                            + json.dumps(
                                {
                                    "status": "progress",
                                    "text": (
                                        f"Claude exited (code={proc.returncode}); "
                                        "finishing run."
                                    ),
                                }
                            )
                            + "\n\n"
                        )
                        break
        finally:
            poll_task.cancel()
            # leave out_task; process may still be writing
            try:
                await poll_task
            except Exception:
                pass

        if early_done:
            # Soft-terminate Claude; graph is already on the board.
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=8.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            yield (
                "data: "
                + json.dumps(
                    {
                        "status": "done",
                        "board_id": board_id,
                        "session_id": session_id,
                        "mode": body.mode.value,
                        "early": True,
                        "nodes": last_node_count if last_node_count >= 0 else None,
                        "warning": (
                            "research completed with 0 nodes written"
                            if last_node_count <= baseline
                            else None
                        ),
                    }
                )
                + "\n\n"
            )
        else:
            await proc.wait()
            # cancel stdout reader
            out_task.cancel()
            final_count = await _count_nodes()
            if proc.returncode == 0 or (final_count is not None and final_count > baseline):
                yield (
                    "data: "
                    + json.dumps(
                        {
                            "status": "done",
                            "board_id": board_id,
                            "session_id": session_id,
                            "mode": body.mode.value,
                            "nodes": final_count if final_count >= 0 else None,
                        }
                    )
                    + "\n\n"
                )
            else:
                stderr_out = b""
                if proc.stderr:
                    stderr_out = await proc.stderr.read()
                # still succeed soft if nodes exist
                if final_count > baseline:
                    yield (
                        "data: "
                        + json.dumps(
                            {
                                "status": "done",
                                "board_id": board_id,
                                "session_id": session_id,
                                "mode": body.mode.value,
                                "nodes": final_count,
                                "warning": "claude exit non-zero but board has nodes",
                            }
                        )
                        + "\n\n"
                    )
                else:
                    yield (
                        "data: "
                        + json.dumps(
                            {
                                "status": "error",
                                "detail": stderr_out.decode()[:500] or f"exit {proc.returncode}",
                            }
                        )
                        + "\n\n"
                    )
    except Exception as exc:
        logger.exception("research_runner: claude failed mode=%s board=%s", body.mode, board_id)
        yield f"data: {json.dumps({'status': 'error', 'detail': str(exc)})}\n\n"
    finally:
        # Reap the subprocess + stdout reader on ANY exit path, including
        # client disconnect (GeneratorExit is BaseException, so the
        # `except Exception` above does NOT catch it). Without this a dropped
        # SSE connection orphans the `claude` process until it self-exits.
        proc = locals().get("proc")
        if proc is not None and proc.returncode is None:
            try:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    proc.kill()
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=3.0)
                    except Exception:
                        pass
            except Exception:
                pass
        out_task = locals().get("out_task")
        if out_task is not None and not out_task.done():
            out_task.cancel()
            try:
                await out_task
            except Exception:
                pass
        clear_session(session_id)
        if body.mode in (ResearchMode.EXPAND, ResearchMode.IMPROVE):
            end_scope(board_id, session_id)
