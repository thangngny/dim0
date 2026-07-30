"""Integration API router for external agent access (Claude CLI ↔ Dim0).

Provides a token-authenticated HTTP API that sits on top of AgentBoardBridge
so external agents (Claude CLI via MCP) can create, update, and delete nodes
and edges with realtime WebSocket broadcast.

Authentication: static bearer token from DIM0_INTEGRATION_TOKEN env var.
    Header: X-Integration-Token: <token>

This router is enabled by default in local/dev stages only. To enable in
production set DIM0_INTEGRATION_ENABLED=true explicitly.

Idempotency: callers may include idempotency_key in requests. The server
stores the result in Redis (TTL 1h) and returns it on retry without
re-applying the mutation.

Security:
  - Constant-time token comparison (hmac.compare_digest)
  - Token never logged
  - Content redacted before persisting (API keys, JWTs, etc.)
  - No access to user management endpoints
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import time
import uuid

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from topix.collab.agent_bridge import AgentBoardBridge
from topix.datatypes.note.note import Note
from topix.datatypes.note.link import Link
from topix.datatypes.resource import RichText
from topix.store.graph import GraphStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integration", tags=["integration"])

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

_INTEGRATION_TOKEN_ENV = "DIM0_INTEGRATION_TOKEN"


def _get_token() -> str:
    t = os.getenv(_INTEGRATION_TOKEN_ENV, "")
    if not t:
        raise RuntimeError(
            f"DIM0_INTEGRATION_TOKEN is not set. "
            f"Set it in .env before using the integration API."
        )
    return t


def _verify_token(request: Request) -> None:
    """Constant-time token check. Raises 401 on mismatch or missing."""
    provided = request.headers.get("X-Integration-Token", "")
    expected = _get_token()
    if not hmac.compare_digest(provided.encode(), expected.encode()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Integration-Token",
        )


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

_REDACT_PATTERNS = [
    re.compile(r"(?i)(sk-[a-zA-Z0-9_-]{20,})", re.MULTILINE),      # OpenAI-style keys
    re.compile(r"(?i)(Bearer\s+[a-zA-Z0-9._-]{20,})", re.MULTILINE),
    re.compile(r"(?i)(Authorization:\s*\S+)", re.MULTILINE),
    re.compile(r"(?i)(api[_-]?key\s*[:=]\s*\S+)", re.MULTILINE),
    re.compile(r"(?i)(password\s*[:=]\s*\S+)", re.MULTILINE),
    re.compile(r"(?i)(secret\s*[:=]\s*\S+)", re.MULTILINE),
    re.compile(r"(?i)(token\s*[:=]\s*[a-zA-Z0-9._-]{16,})", re.MULTILINE),
    re.compile(r"-----BEGIN\s+[A-Z ]+KEY-----.*?-----END\s+[A-Z ]+KEY-----", re.DOTALL),
]


def redact_content(text: str) -> tuple[str, bool]:
    """Redact sensitive patterns. Returns (redacted_text, was_redacted)."""
    if not text:
        return text, False
    original = text
    for pattern in _REDACT_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text, text != original


# ---------------------------------------------------------------------------
# Node kind → shape/color live in research_style.KIND_VISUALS (visual system).


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class NodeInput(BaseModel):
    client_ref: str = Field(..., description="Client-side reference for edge resolution")
    kind: str = Field(default="note", description="Semantic type: question, finding, source, etc.")
    title: str | None = Field(default=None, description="Short title (label)")
    content: str | None = Field(default=None, description="Main body (markdown)")
    x: float | None = Field(default=None)
    y: float | None = Field(default=None)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EdgeInput(BaseModel):
    source_ref: str = Field(..., description="client_ref of source node")
    target_ref: str = Field(..., description="client_ref of target node")
    relation: str | None = Field(default=None, description="Edge label/relation type")


class BatchCreateRequest(BaseModel):
    session_id: str | None = None
    idempotency_key: str | None = None
    nodes: list[NodeInput] = Field(default_factory=list)
    edges: list[EdgeInput] = Field(default_factory=list)


class NodeResult(BaseModel):
    client_ref: str
    node_id: str
    created: bool


class EdgeResult(BaseModel):
    source_ref: str
    target_ref: str
    edge_id: str
    created: bool


class BatchCreateResponse(BaseModel):
    nodes: list[NodeResult]
    edges: list[EdgeResult]
    redacted: bool = False


class NodePatchRequest(BaseModel):
    title: str | None = None
    content: str | None = None
    metadata: dict[str, Any] | None = None


class ResearchEvent(BaseModel):
    """Structured progress event for deep-research live UI (SSE + canvas)."""

    session_id: str
    event_type: str = Field(
        ...,
        description=(
            "One of: planning, workstream_started, source_found, finding_added, "
            "cross_checking, synthesizing, agent_started, agent_progress, "
            "agent_done, agent_failed, completed, failed, cancelled"
        ),
    )
    label: str | None = None
    board_id: str | None = None
    # Sub-agent card fields (optional — inferred when omitted).
    agent_id: str | None = Field(
        default=None,
        description="Stable id for a sub-agent/workstream card, e.g. ws-tv, critique",
    )
    role: str | None = Field(
        default=None,
        description="lead | workstream | collector | critique | writer | worker",
    )
    detail: str | None = Field(
        default=None,
        description="Short human line: what this agent is doing now",
    )
    query: str | None = Field(
        default=None,
        description="Search query / topic fragment the agent is pursuing",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bridge(request: Request) -> AgentBoardBridge:
    return request.app.agent_board_bridge


def _graph_store(request: Request) -> GraphStore:
    return request.app.graph_store


async def _build_note(
    graph_store: GraphStore,
    board_id: str,
    node: NodeInput,
    *,
    default_phase: str | None = None,
    session_id: str | None = None,
) -> tuple[Note, bool]:
    """Construct a Note from a NodeInput. Returns (note, was_redacted).

    Stamps research metadata (kind/phase/citations/…) into content for
    iterative reframe/expand lineage.
    """
    from topix.agents.notes.service import build_note, get_default_note_size
    from topix.datatypes.property import SizeProperty
    from topix.integrations.research_meta import merge_research_metadata, stamp_content
    from topix.integrations.research_style import (
        build_research_style,
        get_kind_visual,
        pretty_title,
    )
    from topix.utils.graph.text_measure import estimate_node_size

    meta = merge_research_metadata(
        node.kind,
        node.metadata,
        phase=default_phase,
        session_id=session_id,
    )
    kind = meta.normalized_kind()
    vis = get_kind_visual(kind)
    node_type = vis.shape

    raw_title = node.title or ""
    raw_content = node.content or ""

    title_clean, title_redacted = redact_content(raw_title)
    content_clean, content_redacted = redact_content(raw_content)
    was_redacted = title_redacted or content_redacted

    label_text = pretty_title(kind, title_clean)
    full_content = stamp_content(content_clean, meta)

    note = await build_note(
        graph_store=graph_store,
        graph_uid=board_id,
        label=label_text,
        content=full_content,
        note_type=node_type,
        parent_id=None,
    )
    # Apply research visual system (overrides random fill from build_note).
    note.style = build_research_style(kind)

    # Re-fit size for the research shape + sans-serif body.
    width, height = get_default_note_size(node_type)
    fitted = estimate_node_size(
        node_type,
        width,
        full_content or label_text,
        note.style.font_size,
    )
    if fitted is not None:
        width, height = fitted
    # Slightly wider cards for source/summary readability
    if kind in ("source", "evidence", "summary", "decision"):
        width = max(width, 340)
        height = max(height, 160)
    note.properties.node_size = SizeProperty(
        size=SizeProperty.Size(width=width, height=height)
    )

    if node.x is not None and node.y is not None:
        from topix.datatypes.property import PositionProperty
        note.properties.node_position = PositionProperty(
            position=PositionProperty.Position(x=node.x, y=node.y)
        )

    return note, was_redacted



# ---------------------------------------------------------------------------
# Idempotency (in-memory for dev; Redis could be added for persistence)
# ---------------------------------------------------------------------------

_idempotency_cache: dict[str, tuple[float, Any]] = {}
_IDEMPOTENCY_TTL = 3600.0  # 1 hour


def _idempotency_key(board_id: str, key: str) -> str:
    return hashlib.sha256(f"{board_id}:{key}".encode()).hexdigest()


def _check_idempotency(cache_key: str) -> Any | None:
    """Return cached response if the idempotency key was seen recently."""
    entry = _idempotency_cache.get(cache_key)
    if entry:
        ts, result = entry
        if time.time() - ts < _IDEMPOTENCY_TTL:
            return result
        del _idempotency_cache[cache_key]
    return None


def _store_idempotency(cache_key: str, result: Any) -> None:
    # Prune old entries to prevent unbounded growth
    now = time.time()
    expired = [k for k, (ts, _) in _idempotency_cache.items() if now - ts >= _IDEMPOTENCY_TTL]
    for k in expired:
        del _idempotency_cache[k]
    _idempotency_cache[cache_key] = (now, result)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/health")
async def integration_health(request: Request):
    """Health check — no auth required."""
    return {
        "status": "ok",
        "agent_bridge": request.app.agent_board_bridge is not None,
        "timestamp": time.time(),
    }


@router.get("/boards/{board_id}")
async def get_board(
    board_id: str,
    request: Request,
    _: None = Depends(_verify_token),
):
    """Return board metadata and node/edge summary."""
    graph_store: GraphStore = _graph_store(request)
    graph = await graph_store.get_graph(board_id)
    if graph is None:
        raise HTTPException(status_code=404, detail="Board not found")
    nodes = [n for n in graph.nodes if n.deleted_at is None]
    edges = [e for e in graph.edges if e.deleted_at is None]
    return {
        "board_id": board_id,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": [
            {
                "id": n.id,
                "kind": n.type,
                "label": n.label.markdown if n.label else None,
                "content": n.content.markdown if n.content else None,
            }
            for n in nodes
        ],
        "edges": [
            {
                "id": e.id,
                "source": e.source,
                "target": e.target,
                "label": e.label.markdown if e.label else None,
            }
            for e in edges
        ],
    }


@router.get("/boards/{board_id}/nodes")
async def list_nodes(
    board_id: str,
    request: Request,
    _: None = Depends(_verify_token),
):
    """List all non-deleted nodes on a board."""
    graph_store: GraphStore = _graph_store(request)
    graph = await graph_store.get_graph(board_id)
    if graph is None:
        raise HTTPException(status_code=404, detail="Board not found")
    nodes = [n for n in graph.nodes if n.deleted_at is None]
    return {
        "board_id": board_id,
        "nodes": [
            {
                "id": n.id,
                "kind": n.type,
                "label": n.label.markdown if n.label else None,
                "content": n.content.markdown if n.content else None,
            }
            for n in nodes
        ],
    }


@router.post("/boards/{board_id}/nodes:batch", response_model=BatchCreateResponse)
async def batch_create(
    board_id: str,
    body: BatchCreateRequest,
    request: Request,
    _: None = Depends(_verify_token),
):
    """Batch-create nodes and edges with idempotency and realtime broadcast.

    Uses client_ref strings to resolve edge source/target to real Dim0 IDs.
    Idempotency_key prevents duplicate creation on retry.
    """
    bridge: AgentBoardBridge = _bridge(request)
    graph_store: GraphStore = _graph_store(request)

    # Idempotency check
    idem_result = None
    cache_key: str | None = None
    if body.idempotency_key:
        cache_key = _idempotency_key(board_id, body.idempotency_key)
        idem_result = _check_idempotency(cache_key)
        if idem_result is not None:
            logger.info("integration: idempotency hit key=%s board=%s", body.idempotency_key, board_id)
            return idem_result

    node_results: list[NodeResult] = []
    edge_results: list[EdgeResult] = []
    ref_to_id: dict[str, str] = {}
    overall_redacted = False

    # Expand-scope create budget (if an expand research session is active)
    from topix.integrations.research_scope import assert_can_create, note_created

    # Dedupe source/evidence by URL: reuse an existing node id instead of
    # creating a duplicate source the agent already added earlier.
    from topix.integrations.research_citation import (
        build_existing_url_index,
        plan_dedup,
    )

    existing_graph = await graph_store.get_graph(board_id)
    existing_nodes = [
        {
            "id": n.id,
            "kind": n.type,
            "content": (n.content.markdown if n.content else ""),
            "label": (n.label.markdown if n.label else ""),
        }
        for n in (existing_graph.nodes if existing_graph else [])
        if n.deleted_at is None
    ] or []
    existing_url_index = build_existing_url_index(existing_nodes)
    new_dicts = [
        {"client_ref": n.client_ref, "kind": n.kind, "metadata": n.metadata}
        for n in body.nodes
    ]
    nodes_to_create_dicts, reuse_map = plan_dedup(new_dicts, existing_url_index)
    nodes_to_create = [
        n for n in body.nodes if n.client_ref not in reuse_map
    ]
    # Edges and refs resolve reused ids transparently.
    for client_ref, existing_id in reuse_map.items():
        ref_to_id[client_ref] = existing_id
        node_results.append(NodeResult(
            client_ref=client_ref, node_id=existing_id, created=False,
        ))
        logger.info(
            "integration: deduped source client_ref=%s -> existing node=%s",
            client_ref, existing_id,
        )

    try:
        assert_can_create(board_id, len(nodes_to_create))
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    # Create nodes
    notes_to_add: list[Note] = []
    node_meta: list[tuple[str, bool, str]] = []  # (client_ref, was_redacted, kind)

    for node_input in nodes_to_create:
        try:
            note, was_redacted = await _build_note(
                graph_store,
                board_id,
                node_input,
                session_id=body.session_id,
            )
            notes_to_add.append(note)
            kind = (node_input.kind or "note").lower()
            node_meta.append((node_input.client_ref, was_redacted, kind))
            if was_redacted:
                overall_redacted = True
        except Exception:
            logger.exception("integration: failed to build note client_ref=%s", node_input.client_ref)
            raise HTTPException(status_code=422, detail=f"Invalid node '{node_input.client_ref}'")

    kind_by_id: dict[str, str] = {}
    if notes_to_add:
        try:
            await bridge.add_notes(board_id=board_id, notes=notes_to_add)
        except Exception:
            logger.exception("integration: add_notes failed board=%s", board_id)
            raise HTTPException(status_code=500, detail="Failed to create nodes")

        created_ids: list[str] = []
        for note, (client_ref, was_redacted, kind) in zip(notes_to_add, node_meta):
            ref_to_id[client_ref] = note.id
            created_ids.append(note.id)
            kind_by_id[note.id] = kind
            node_results.append(NodeResult(
                client_ref=client_ref,
                node_id=note.id,
                created=True,
            ))
            logger.info(
                "integration: created node board=%s kind=%s ref=%s id=%s redacted=%s",
                board_id, note.type, client_ref, note.id, was_redacted,
            )
        note_created(board_id, created_ids)

    # Create edges (research-styled arrows)
    from topix.integrations.research_layout import decorate_research_link

    links_to_add: list[Link] = []
    edge_meta: list[tuple[str, str]] = []  # (source_ref, target_ref)

    for edge_input in body.edges:
        source_id = ref_to_id.get(edge_input.source_ref)
        target_id = ref_to_id.get(edge_input.target_ref)
        if not source_id:
            raise HTTPException(
                status_code=422,
                detail=f"Edge source_ref '{edge_input.source_ref}' not found in this batch or unknown.",
            )
        if not target_id:
            raise HTTPException(
                status_code=422,
                detail=f"Edge target_ref '{edge_input.target_ref}' not found in this batch or unknown.",
            )
        relation_label, _ = redact_content(edge_input.relation or "")
        link = decorate_research_link(
            source_id=source_id,
            target_id=target_id,
            board_id=board_id,
            relation=relation_label,
        )
        links_to_add.append(link)
        edge_meta.append((edge_input.source_ref, edge_input.target_ref))

    if links_to_add:
        try:
            await bridge.add_links(board_id=board_id, links=links_to_add)
        except Exception:
            logger.exception("integration: add_links failed board=%s", board_id)
            raise HTTPException(status_code=500, detail="Failed to create edges")

        for link, (source_ref, target_ref) in zip(links_to_add, edge_meta):
            edge_results.append(EdgeResult(
                source_ref=source_ref,
                target_ref=target_ref,
                edge_id=link.id,
                created=True,
            ))

    # Hierarchical research layout (presentation): Question → WS → Finding → Source → Summary
    if kind_by_id:
        try:
            from topix.integrations.research_layout import apply_research_layout

            await apply_research_layout(
                graph_store=graph_store,
                bridge=bridge,
                board_id=board_id,
                created_ids=list(kind_by_id.keys()),
                kind_by_id=kind_by_id,
            )
        except Exception:
            logger.exception("integration: research layout failed board=%s", board_id)

    response = BatchCreateResponse(
        nodes=node_results,
        edges=edge_results,
        redacted=overall_redacted,
    )

    if cache_key:
        _store_idempotency(cache_key, response)

    return response


@router.post("/boards/{board_id}/nodes:resolve_refs")
async def resolve_refs(
    board_id: str,
    body: dict[str, str],  # {client_ref -> existing_node_id}
    request: Request,
    _: None = Depends(_verify_token),
):
    """Register existing node IDs for a client_ref mapping.

    Allows follow-up batches to reference nodes created in previous batches
    by looking them up by ID. Returns the mapping back.
    """
    return {"resolved": body, "board_id": board_id}


@router.patch("/boards/{board_id}/nodes/{node_id}")
async def update_node(
    board_id: str,
    node_id: str,
    body: NodePatchRequest,
    request: Request,
    _: None = Depends(_verify_token),
):
    """Patch an existing node with title/content updates."""
    from topix.integrations.research_scope import assert_can_mutate

    try:
        assert_can_mutate(board_id, node_id)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    bridge: AgentBoardBridge = _bridge(request)

    patch: dict[str, Any] = {}
    redacted = False

    if body.title is not None:
        title_clean, was_r = redact_content(body.title)
        if was_r:
            redacted = True
        patch["label"] = {"markdown": title_clean}

    if body.content is not None:
        content_clean, was_r = redact_content(body.content)
        if was_r:
            redacted = True
        patch["content"] = {"markdown": content_clean}

    if not patch:
        raise HTTPException(status_code=422, detail="No fields to update")

    updated = await bridge.patch_note(board_id=board_id, node_id=node_id, data=patch, user_uid=None)
    if updated is None:
        raise HTTPException(status_code=404, detail="Node not found")

    return {
        "node_id": node_id,
        "updated": True,
        "redacted": redacted,
    }


@router.delete("/boards/{board_id}/nodes/{node_id}")
async def delete_node(
    board_id: str,
    node_id: str,
    request: Request,
    _: None = Depends(_verify_token),
):
    """Delete a node from the board."""
    from topix.integrations.research_scope import assert_can_mutate

    try:
        assert_can_mutate(board_id, node_id)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    bridge: AgentBoardBridge = _bridge(request)
    await bridge.delete_node(board_id=board_id, node_id=node_id, user_uid=None)
    return {"node_id": node_id, "deleted": True}


@router.delete("/boards/{board_id}/edges/{edge_id}")
async def delete_edge(
    board_id: str,
    edge_id: str,
    request: Request,
    _: None = Depends(_verify_token),
):
    """Delete an edge from the board."""
    bridge: AgentBoardBridge = _bridge(request)
    await bridge.delete_link(board_id=board_id, link_id=edge_id)
    return {"edge_id": edge_id, "deleted": True}


@router.post("/boards/{board_id}/layout")
async def layout_board(
    board_id: str,
    body: dict[str, Any],
    request: Request,
    _: None = Depends(_verify_token),
):
    """Trigger auto-layout for recently created nodes.

    Expects: {"created_ids": [...], "created_link_ids": [...], "mode": "research"|...}
    `mode=research` uses hierarchical research presentation layout.
    """
    from topix.agents.notes.layout import rearrange_created_notes
    graph_store: GraphStore = _graph_store(request)
    bridge: AgentBoardBridge = _bridge(request)

    created_ids: list[str] = body.get("created_ids", [])
    created_link_ids: list[str] = body.get("created_link_ids", [])
    mode = (body.get("mode") or "default").lower()

    if not created_ids:
        return {"moved": [], "count": 0}

    if mode == "research":
        from topix.integrations.research_layout import apply_research_layout

        moved = await apply_research_layout(
            graph_store=graph_store,
            bridge=bridge,
            board_id=board_id,
            created_ids=created_ids,
        )
        return {"moved": moved, "count": len(moved), "mode": "research"}

    moved = await rearrange_created_notes(
        graph_store=graph_store,
        graph_uid=board_id,
        created_ids=created_ids,
        created_link_ids=created_link_ids or None,
        agent_bridge=bridge,
    )
    return {"moved": moved, "count": len(moved)}


@router.post("/boards/{board_id}/research-events")
async def research_event(
    board_id: str,
    event: ResearchEvent,
    request: Request,
    _: None = Depends(_verify_token),
):
    """Log a structured research event and push it to live canvas clients."""
    from topix.integrations.research_progress import (
        get_progress,
        record_event,
        snapshot_dict,
    )

    # Prefer path board_id; fall back to body if clients send only body board.
    target_board = board_id or event.board_id or ""
    stored = record_event(
        session_id=event.session_id,
        board_id=target_board,
        event_type=event.event_type,
        label=event.label,
        agent_id=event.agent_id,
        role=event.role,
        detail=event.detail,
        query=event.query,
    )
    logger.info(
        "integration: research_event board=%s session=%s type=%s agent=%s label=%s",
        target_board,
        event.session_id,
        event.event_type,
        stored.agent_id,
        event.label,
    )

    # Live board panel: broadcast full session snapshot (agents + last event).
    if target_board:
        try:
            bridge = _bridge(request)
            prog = get_progress(event.session_id)
            payload = {
                "session_id": event.session_id,
                "event": {
                    "id": stored.id,
                    "event_type": stored.event_type,
                    "label": stored.label,
                    "agent_id": stored.agent_id,
                    "role": stored.role,
                    "detail": stored.detail,
                    "query": stored.query,
                    "ts": stored.ts,
                },
            }
            if prog is not None:
                payload["snapshot"] = snapshot_dict(prog)
            await bridge.broadcast_research_progress(
                board_id=target_board,
                payload=payload,
            )
        except Exception:
            logger.exception(
                "integration: research-progress broadcast failed board=%s",
                target_board,
            )

    return {
        "received": True,
        "event_type": event.event_type,
        "board_id": target_board,
        "agent_id": stored.agent_id,
        "event_id": stored.id,
    }


@router.get("/boards/{board_id}/research-progress")
async def get_research_progress(
    board_id: str,
    session_id: str | None = None,
    _: None = Depends(_verify_token),
):
    """Poll live research agent cards + timeline for a board/session."""
    from topix.integrations.research_progress import (
        get_board_progress,
        snapshot_dict,
    )

    prog = get_board_progress(board_id, session_id=session_id)
    if prog is None:
        return {
            "board_id": board_id,
            "session_id": session_id,
            "active": False,
            "agents": [],
            "events": [],
        }
    snap = snapshot_dict(prog)
    snap["active"] = not (prog.completed or prog.failed)
    return snap


# ---------------------------------------------------------------------------
# /research + /generate — multi-mode board research via Claude CLI + MCP
# ---------------------------------------------------------------------------

from fastapi.responses import StreamingResponse

from topix.integrations.research_clarify import ClarifyRequest, run_clarify
from topix.integrations.research_plan import PlanRequest, run_plan
from topix.integrations.research_runner import (
    ResearchBudget,
    ResearchMode,
    ResearchRequest,
    stream_research_claude,
)


class GenerateRequest(BaseModel):
    """Backward-compatible explore entrypoint (creates first graph from topic)."""

    topic: str = Field(..., description="Research topic to visualize")
    language: str = Field(default="vi", description="Response language: 'vi' or 'en'")
    session_id: str | None = None


def _sse_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    }


@router.post("/research/clarify")
async def research_clarify(
    body: ClarifyRequest,
    _: None = Depends(_verify_token),
):
    """Interactive clarify gate: Claude CLI asks back per gap, then scope fold.

    stage=questions → Claude CLI reads the board (board_id/mode/focus_node_ids)
    and returns 0–4 personalized questions (or clear=true); falls back to
    Ollama/static. stage=scope → deterministic fold of topic + answers.
    """
    return await run_clarify(body)


@router.post("/research/plan")
async def research_plan(
    body: PlanRequest,
    _: None = Depends(_verify_token),
):
    """Approve-before-run gate: produce a structured execution plan (workstreams +
    search strategy + intended sources) the launcher shows before the full
    research SSE fires. Reuses the clarify LiteLLM JSON path.
    """
    return await run_plan(body)


class SetKindRequest(BaseModel):
    kind: str


class ReparentRequest(BaseModel):
    parent_id: str | None = None


class MergeRequest(BaseModel):
    node_ids: list[str]
    target_id: str
    confirm: bool = False


class SplitRequest(BaseModel):
    parts: list[str]
    confirm: bool = False
    delete_original: bool = True


@router.post("/boards/{board_id}/nodes/{node_id}:set-kind")
async def set_node_kind(
    board_id: str, node_id: str, body: SetKindRequest, request: Request,
    _: None = Depends(_verify_token),
):
    """Re-style a node to a research kind (shape + color + size)."""
    from topix.integrations.research_scope import assert_can_mutate
    try:
        assert_can_mutate(board_id, node_id)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    bridge: AgentBoardBridge = _bridge(request)
    updated = await bridge.change_note_kind(
        board_id=board_id, node_id=node_id, kind=body.kind, user_uid=None)
    if updated is None:
        raise HTTPException(status_code=404, detail="Node not found")
    return {"node_id": node_id, "kind": body.kind, "updated": True}


@router.post("/boards/{board_id}/nodes/{node_id}:reparent")
async def reparent_node(
    board_id: str, node_id: str, body: ReparentRequest, request: Request,
    _: None = Depends(_verify_token),
):
    """Move a node under a new parent (or to the board root)."""
    from topix.integrations.research_scope import assert_can_mutate
    try:
        assert_can_mutate(board_id, node_id)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    bridge: AgentBoardBridge = _bridge(request)
    try:
        updated = await bridge.reparent_note(
            board_id=board_id, node_id=node_id, new_parent_id=body.parent_id, user_uid=None)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="Node not found")
    return {"node_id": node_id, "parent_id": updated.parent_id, "updated": True}


@router.delete("/boards/{board_id}/nodes/{node_id}:subtree")
async def delete_subtree_ep(
    board_id: str, node_id: str, request: Request,
    _: None = Depends(_verify_token),
):
    """Preview (default) then delete a node + its descendants.

    Pass `?confirm=true` to execute. Without it, returns the affected counts.
    """
    from topix.integrations.research_scope import assert_can_mutate
    try:
        assert_can_mutate(board_id, node_id)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    bridge: AgentBoardBridge = _bridge(request)
    confirm = request.query_params.get("confirm", "").lower() == "true"
    try:
        result = await bridge.delete_subtree(
            board_id=board_id, node_id=node_id, confirm=confirm, user_uid=None)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return result


@router.post("/boards/{board_id}/nodes:merge")
async def merge_nodes_ep(
    board_id: str, body: MergeRequest, request: Request,
    _: None = Depends(_verify_token),
):
    """Merge several nodes into one target node (two-phase via body.confirm)."""
    from topix.integrations.research_scope import assert_can_mutate

    # Guard every node touched by the merge (target_id is in node_ids).
    for nid in body.node_ids:
        try:
            assert_can_mutate(board_id, nid)
        except ValueError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
    bridge: AgentBoardBridge = _bridge(request)
    try:
        result = await bridge.merge_notes(
            board_id=board_id, node_ids=body.node_ids, target_id=body.target_id,
            confirm=body.confirm, user_uid=None)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return result


@router.post("/boards/{board_id}/nodes/{node_id}:split")
async def split_node_ep(
    board_id: str, node_id: str, body: SplitRequest, request: Request,
    _: None = Depends(_verify_token),
):
    """Split one node into several sibling notes (two-phase via body.confirm)."""
    from topix.integrations.research_scope import (
        assert_can_create,
        assert_can_mutate,
        note_created,
    )
    try:
        assert_can_mutate(board_id, node_id)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    # Expand-scope create budget for the new split notes.
    try:
        assert_can_create(board_id, len(body.parts))
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    # Redact each chunk before handing to the bridge.
    body.parts = [redact_content(p)[0] for p in body.parts]
    bridge: AgentBoardBridge = _bridge(request)
    try:
        result = await bridge.split_note(
            board_id=board_id, node_id=node_id, parts=body.parts,
            confirm=body.confirm, delete_original=body.delete_original, user_uid=None)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # Register newly created notes with the active expand scope.
    created_ids = result.get("created_ids") if isinstance(result, dict) else None
    if created_ids:
        note_created(board_id, created_ids)
    return result


@router.post("/boards/{board_id}/research")
async def run_board_research(
    board_id: str,
    body: ResearchRequest,
    request: Request,
    _: None = Depends(_verify_token),
):
    """Multi-mode research controller: explore | reframe | expand | critique.

    Streams SSE progress. Same board for reframe/expand/critique (delta writes).
    """
    return StreamingResponse(
        stream_research_claude(board_id=board_id, body=body),
        media_type="text/event-stream",
        headers=_sse_headers(),
    )


@router.post("/boards/{board_id}/generate")
async def generate_research_graph(
    board_id: str,
    body: GenerateRequest,
    request: Request,
    _: None = Depends(_verify_token),
):
    """Compat wrapper: explore mode for a fresh topic (launcher step 2)."""
    research = ResearchRequest(
        mode=ResearchMode.EXPLORE,
        instruction=body.topic[:4000],
        language=body.language,
        session_id=body.session_id,
        budget=ResearchBudget(max_new_nodes=24, effort="ultracode"),
    )
    return StreamingResponse(
        stream_research_claude(board_id=board_id, body=research),
        media_type="text/event-stream",
        headers=_sse_headers(),
    )
