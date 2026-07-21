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
    return "high"


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
        "9) Emit dim0_emit_research_event for major phases (planning, synthesizing, completed).\n"
        "10) Do NOT paste chain-of-thought onto the board — only final graph content.\n"
        "11) Expand scope: if MODE=expand, never update/delete nodes outside FOCUS_NODE_IDS "
        "(server will reject). New children are allowed under focus.\n"
        f"12) ALWAYS pass board_id=\"{board_id}\" on EVERY Dim0 MCP tool call "
        f"(get_board, upsert, create, update, delete, layout, research_events). "
        f"Never omit board_id. Never write to another board.\n"
        "13) When finished writing the graph, emit research_event event_type=completed "
        "with a short label (required for UI to finish promptly).\n"
        "14) PRESENTATION: titles ≤ 60 chars, one idea per node, short body (3–6 lines). "
        "Prefer clear hierarchy edges: question→workstream→finding→source, "
        "summary summarizes workstreams. Call dim0_layout_nodes after each batch "
        "(server applies hierarchical research layout automatically).\n"
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
                "tuân thủ tuyệt đối — không lan man ngoài scope; workstream bám research_axes.\n"
                "Tạo research graph: 1 Question (từ problem_statement), 3–6 Workstream, "
                "Source/Evidence (ưu tiên WEB_EVIDENCE), Finding, 1 Unknown/Contradiction, "
                "1 Decision, 1 Summary.\n"
                "Orchestrate sub-agents: plan → collect song song → critique → write.\n"
                f"idempotency_key='{session_id}-explore'.\n"
                "Tiếng Việt, progress ngắn."
            )
        else:
            task = (
                "MODE=explore — first pass RESPECTING LOCKED SCOPE in INSTRUCTION.\n"
                "If SCOPE / IN_SCOPE / OUT_OF_SCOPE / AXES present: obey strictly; "
                "workstreams follow research axes only.\n"
                "Build graph: 1 Question, 3–6 Workstreams, Sources/Evidence from WEB_EVIDENCE, "
                "Findings, Unknown/Contradiction, Decision, Summary.\n"
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

    # Expand scope gate registration
    if body.mode == ResearchMode.EXPAND:
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
            yield (
                "data: "
                + json.dumps(
                    {
                        "status": "progress",
                        "text": (
                            f"WEB_EVIDENCE engines={briefing.get('engines')} "
                            f"results={len(briefing.get('results') or [])} "
                            f"available={briefing.get('available')}"
                        ),
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
    }.get(body.mode, body.mode.value)

    from topix.integrations.research_progress import (
        clear_session,
        get_progress,
        set_nodes_seen,
        track_session,
    )

    track_session(session_id, board_id, body.mode.value)

    yield (
        "data: "
        + json.dumps(
            {
                "status": "running",
                "message": f"Claude [{effort}] · {mode_label}…",
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
                    prog = get_progress(session_id)
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

                    # Early done: MCP completed event
                    if prog and prog.completed:
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

                if proc.returncode is not None and kind != "stdout":
                    # process ended; drain remaining handled by stdout_done
                    pass
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
        clear_session(session_id)
        if body.mode == ResearchMode.EXPAND:
            end_scope(board_id, session_id)
