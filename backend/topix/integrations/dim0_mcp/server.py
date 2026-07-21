"""Dim0 MCP server for Claude CLI.

Exposes Dim0 board operations as MCP tools over stdio transport.
The server calls the Dim0 integration HTTP API — no direct DB access.

Configuration via environment:
  DIM0_BASE_URL          - base URL of the Dim0 backend (default: http://127.0.0.1:8081)
  DIM0_INTEGRATION_TOKEN - integration token (required)
  DIM0_DEFAULT_BOARD_ID  - default board ID to use when not specified

Usage (stdio transport, for Claude CLI):
  python -m topix.integrations.dim0_mcp.server

Log output goes to stderr to avoid polluting the MCP stdio stream.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
import uuid

from typing import Any

import httpx

# ── MCP SDK ──────────────────────────────────────────────────────────────────
# We implement a minimal MCP stdio server from scratch to avoid adding a
# heavy dependency. The protocol is simple JSON-RPC 2.0 over stdin/stdout.
# ─────────────────────────────────────────────────────────────────────────────

# Configure logging to stderr only (stdout is the MCP channel)
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s dim0-mcp %(levelname)s %(message)s",
)
logger = logging.getLogger("dim0_mcp")

# ── Config ───────────────────────────────────────────────────────────────────

BASE_URL = os.getenv("DIM0_BASE_URL", "http://127.0.0.1:8081").rstrip("/")
TOKEN = os.getenv("DIM0_INTEGRATION_TOKEN", "")
DEFAULT_BOARD_ID = os.getenv("DIM0_DEFAULT_BOARD_ID", "")
REQUEST_TIMEOUT = 60.0


def _headers() -> dict[str, str]:
    return {
        "X-Integration-Token": TOKEN,
        "Content-Type": "application/json",
    }


# ── HTTP client ───────────────────────────────────────────────────────────────

_http: httpx.AsyncClient | None = None


def _client() -> httpx.AsyncClient:
    global _http
    if _http is None or _http.is_closed:
        _http = httpx.AsyncClient(timeout=REQUEST_TIMEOUT)
    return _http


async def _api(method: str, path: str, **kwargs) -> dict:
    """Make an authenticated API call. Raises on HTTP errors."""
    url = f"{BASE_URL}{path}"
    resp = await _client().request(method, url, headers=_headers(), **kwargs)
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text
        raise ValueError(f"API {method} {path} → HTTP {resp.status_code}: {detail}")
    return resp.json()


# ── Tool definitions ─────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "dim0_health",
        "description": "Check if the Dim0 backend and integration API are reachable.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "dim0_get_board",
        "description": (
            "Get the current state of a Dim0 board including all nodes and edges. "
            "Use this to read existing research before adding new content."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "board_id": {
                    "type": "string",
                    "description": "Dim0 board/graph ID. Uses DIM0_DEFAULT_BOARD_ID if omitted.",
                }
            },
            "required": [],
        },
    },
    {
        "name": "dim0_list_nodes",
        "description": "List all nodes on a board with their IDs, kinds, and content summaries.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "board_id": {"type": "string", "description": "Board ID (optional, uses default)."},
            },
            "required": [],
        },
    },
    {
        "name": "dim0_create_nodes",
        "description": (
            "Create one or more research nodes on the Dim0 canvas with optional edges between them. "
            "This is the primary tool for building a research graph. "
            "Returns a mapping of client_ref → actual node IDs for follow-up edge creation. "
            "Supports idempotency_key to prevent duplicate creation on retry.\n\n"
            "Node kinds: question, workstream, source, evidence, finding, hypothesis, "
            "contradiction, unknown, alternative, decision, summary, status, note.\n\n"
            "Edge relations: investigates, derived_from, supports, contradicts, depends_on, "
            "blocks, produces, leads_to, supersedes, summarizes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "board_id": {"type": "string", "description": "Board ID (optional, uses default)."},
                "session_id": {"type": "string", "description": "Research session identifier."},
                "idempotency_key": {
                    "type": "string",
                    "description": "Unique key to prevent duplicate creation on retry.",
                },
                "nodes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "client_ref": {
                                "type": "string",
                                "description": "Your local reference used in edge source_ref/target_ref.",
                            },
                            "kind": {
                                "type": "string",
                                "enum": [
                                    "question", "workstream", "source", "evidence",
                                    "finding", "hypothesis", "contradiction", "unknown",
                                    "alternative", "decision", "summary", "status", "note",
                                ],
                                "description": "Semantic type of this research node.",
                            },
                            "title": {
                                "type": "string",
                                "maxLength": 200,
                                "description": "Short title for the node label.",
                            },
                            "content": {
                                "type": "string",
                                "maxLength": 4000,
                                "description": "Main body markdown. Citations, findings, evidence, etc.",
                            },
                            "metadata": {
                                "type": "object",
                                "description": "Extra structured data: confidence, phase, url, etc.",
                                "properties": {
                                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                                    "phase": {"type": "string"},
                                    "url": {"type": "string"},
                                    "citation": {"type": "string"},
                                },
                            },
                        },
                        "required": ["client_ref", "kind"],
                    },
                    "description": "Nodes to create.",
                    "maxItems": 30,
                },
                "edges": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "source_ref": {"type": "string", "description": "client_ref of source node."},
                            "target_ref": {"type": "string", "description": "client_ref of target node."},
                            "relation": {
                                "type": "string",
                                "description": "Relation label: investigates, supports, contradicts, etc.",
                            },
                        },
                        "required": ["source_ref", "target_ref"],
                    },
                    "description": "Directed edges to create between nodes in this batch.",
                    "maxItems": 50,
                },
            },
            "required": ["nodes"],
        },
    },
    {
        "name": "dim0_update_node",
        "description": "Update the title or content of an existing node.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "node_id": {"type": "string", "description": "Exact Dim0 node ID (from dim0_create_nodes response)."},
                "title": {"type": "string", "maxLength": 200},
                "content": {"type": "string", "maxLength": 4000},
            },
            "required": ["node_id"],
        },
    },
    {
        "name": "dim0_delete_node",
        "description": "Delete a node from the board.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "node_id": {"type": "string"},
            },
            "required": ["node_id"],
        },
    },
    {
        "name": "dim0_delete_edge",
        "description": "Delete an edge from the board.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "edge_id": {"type": "string"},
            },
            "required": ["edge_id"],
        },
    },
    {
        "name": "dim0_layout_nodes",
        "description": (
            "Run auto-layout on recently created nodes so they are arranged clearly on the canvas. "
            "Call this after creating a batch of connected nodes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "created_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of node IDs to arrange (returned by dim0_create_nodes).",
                },
                "created_link_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of edge IDs created in the same batch.",
                },
            },
            "required": ["created_ids"],
        },
    },
    {
        "name": "dim0_emit_research_event",
        "description": (
            "Emit a high-level research status event (no canvas change). "
            "Use this to signal progress: planning, workstream_started, "
            "source_found, finding_added, cross_checking, synthesizing, completed, failed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "board_id": {"type": "string"},
                "session_id": {"type": "string"},
                "event_type": {
                    "type": "string",
                    "enum": [
                        "planning", "workstream_started", "source_found",
                        "finding_added", "cross_checking", "synthesizing",
                        "completed", "failed", "cancelled",
                    ],
                },
                "label": {"type": "string", "maxLength": 200},
            },
            "required": ["session_id", "event_type"],
        },
    },
    {
        "name": "dim0_upsert_research_graph",
        "description": (
            "Primary batch tool: create an entire research graph phase in one call. "
            "Nodes are created, edges are linked, and layout is applied automatically. "
            "Use client_ref strings — the server resolves them to real IDs and returns the mapping. "
            "This is the preferred tool for large research operations with Ollama backend "
            "because it minimizes round-trips. Supports idempotency_key for safe retry."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "board_id": {"type": "string", "description": "Board ID (optional, uses default)."},
                "session_id": {"type": "string"},
                "idempotency_key": {"type": "string"},
                "phase": {
                    "type": "string",
                    "description": "Research phase: planning, collection, synthesis, etc.",
                },
                "nodes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "client_ref": {"type": "string"},
                            "kind": {
                                "type": "string",
                                "enum": [
                                    "question", "workstream", "source", "evidence",
                                    "finding", "hypothesis", "contradiction", "unknown",
                                    "alternative", "decision", "summary", "status", "note",
                                ],
                            },
                            "title": {"type": "string", "maxLength": 200},
                            "content": {"type": "string", "maxLength": 4000},
                            "metadata": {"type": "object"},
                        },
                        "required": ["client_ref", "kind"],
                    },
                    "maxItems": 30,
                },
                "edges": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "source_ref": {"type": "string"},
                            "target_ref": {"type": "string"},
                            "relation": {"type": "string"},
                        },
                        "required": ["source_ref", "target_ref"],
                    },
                    "maxItems": 50,
                },
                "run_layout": {
                    "type": "boolean",
                    "description": "Whether to run auto-layout after creating nodes (default: true).",
                },
            },
            "required": ["nodes"],
        },
    },
]


# ── Tool handlers ─────────────────────────────────────────────────────────────

async def handle_dim0_health(_args: dict) -> dict:
    try:
        result = await _api("GET", "/integration/health")
        return {"status": "ok", "backend": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


async def handle_dim0_get_board(args: dict) -> dict:
    board_id = args.get("board_id") or DEFAULT_BOARD_ID
    if not board_id:
        return {"error": "board_id required — set DIM0_DEFAULT_BOARD_ID or pass board_id"}
    return await _api("GET", f"/integration/boards/{board_id}")


async def handle_dim0_list_nodes(args: dict) -> dict:
    board_id = args.get("board_id") or DEFAULT_BOARD_ID
    if not board_id:
        return {"error": "board_id required"}
    return await _api("GET", f"/integration/boards/{board_id}/nodes")


async def handle_dim0_create_nodes(args: dict) -> dict:
    board_id = args.get("board_id") or DEFAULT_BOARD_ID
    if not board_id:
        return {"error": "board_id required"}
    payload = {
        "session_id": args.get("session_id"),
        "idempotency_key": args.get("idempotency_key"),
        "nodes": args.get("nodes", []),
        "edges": args.get("edges", []),
    }
    result = await _api("POST", f"/integration/boards/{board_id}/nodes:batch", json=payload)
    return result


async def handle_dim0_update_node(args: dict) -> dict:
    board_id = args.get("board_id") or DEFAULT_BOARD_ID
    node_id = args.get("node_id")
    if not board_id or not node_id:
        return {"error": "board_id and node_id required"}
    patch = {}
    if "title" in args:
        patch["title"] = args["title"]
    if "content" in args:
        patch["content"] = args["content"]
    return await _api("PATCH", f"/integration/boards/{board_id}/nodes/{node_id}", json=patch)


async def handle_dim0_delete_node(args: dict) -> dict:
    board_id = args.get("board_id") or DEFAULT_BOARD_ID
    node_id = args.get("node_id")
    if not board_id or not node_id:
        return {"error": "board_id and node_id required"}
    return await _api("DELETE", f"/integration/boards/{board_id}/nodes/{node_id}")


async def handle_dim0_delete_edge(args: dict) -> dict:
    board_id = args.get("board_id") or DEFAULT_BOARD_ID
    edge_id = args.get("edge_id")
    if not board_id or not edge_id:
        return {"error": "board_id and edge_id required"}
    return await _api("DELETE", f"/integration/boards/{board_id}/edges/{edge_id}")


async def handle_dim0_layout_nodes(args: dict) -> dict:
    board_id = args.get("board_id") or DEFAULT_BOARD_ID
    if not board_id:
        return {"error": "board_id required"}
    payload = {
        "created_ids": args.get("created_ids", []),
        "created_link_ids": args.get("created_link_ids", []),
    }
    return await _api("POST", f"/integration/boards/{board_id}/layout", json=payload)


async def handle_dim0_emit_research_event(args: dict) -> dict:
    board_id = args.get("board_id") or DEFAULT_BOARD_ID or "unknown"
    payload = {
        "session_id": args.get("session_id", str(uuid.uuid4())),
        "event_type": args.get("event_type", "planning"),
        "label": args.get("label"),
        "board_id": board_id,
    }
    return await _api("POST", f"/integration/boards/{board_id}/research-events", json=payload)


async def handle_dim0_upsert_research_graph(args: dict) -> dict:
    """Create a complete research graph phase: nodes + edges + auto-layout."""
    board_id = args.get("board_id") or DEFAULT_BOARD_ID
    if not board_id:
        return {"error": "board_id required — set DIM0_DEFAULT_BOARD_ID or pass board_id"}

    session_id = args.get("session_id", str(uuid.uuid4()))
    idem_key = args.get("idempotency_key")
    phase = args.get("phase", "research")
    run_layout = args.get("run_layout", True)

    # Stamp phase/session into each node metadata for research lineage.
    nodes_in = []
    for n in args.get("nodes", []):
        node = dict(n) if isinstance(n, dict) else n
        if isinstance(node, dict):
            meta = dict(node.get("metadata") or {})
            meta.setdefault("phase", phase)
            meta.setdefault("session_id", session_id)
            if node.get("kind"):
                meta.setdefault("kind", node["kind"])
            node["metadata"] = meta
            nodes_in.append(node)
        else:
            nodes_in.append(n)

    # 1. Create nodes + edges
    payload = {
        "session_id": session_id,
        "idempotency_key": idem_key,
        "nodes": nodes_in,
        "edges": args.get("edges", []),
    }
    create_result = await _api(
        "POST", f"/integration/boards/{board_id}/nodes:batch", json=payload
    )

    # 2. Auto-layout if requested
    layout_result = None
    if run_layout:
        node_ids = [n["node_id"] for n in create_result.get("nodes", [])]
        edge_ids = [e["edge_id"] for e in create_result.get("edges", [])]
        if node_ids:
            try:
                layout_result = await _api(
                    "POST",
                    f"/integration/boards/{board_id}/layout",
                    json={
                        "created_ids": node_ids,
                        "created_link_ids": edge_ids,
                        "mode": "research",
                    },
                )
            except Exception as exc:
                logger.warning("Layout failed (non-fatal): %s", exc)

    return {
        "board_id": board_id,
        "session_id": session_id,
        "phase": phase,
        "nodes_created": len(create_result.get("nodes", [])),
        "edges_created": len(create_result.get("edges", [])),
        "ref_to_id": {n["client_ref"]: n["node_id"] for n in create_result.get("nodes", [])},
        "edge_ids": [e["edge_id"] for e in create_result.get("edges", [])],
        "layout": layout_result,
        "redacted": create_result.get("redacted", False),
    }


HANDLERS: dict[str, Any] = {
    "dim0_health": handle_dim0_health,
    "dim0_get_board": handle_dim0_get_board,
    "dim0_list_nodes": handle_dim0_list_nodes,
    "dim0_create_nodes": handle_dim0_create_nodes,
    "dim0_update_node": handle_dim0_update_node,
    "dim0_delete_node": handle_dim0_delete_node,
    "dim0_delete_edge": handle_dim0_delete_edge,
    "dim0_layout_nodes": handle_dim0_layout_nodes,
    "dim0_emit_research_event": handle_dim0_emit_research_event,
    "dim0_upsert_research_graph": handle_dim0_upsert_research_graph,
}


# ── Minimal MCP JSON-RPC 2.0 stdio server ────────────────────────────────────

def _send(obj: dict) -> None:
    """Write a JSON-RPC message to stdout."""
    line = json.dumps(obj, ensure_ascii=False)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def _error_response(req_id: Any, code: int, message: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": code, "message": message},
    }


async def _handle_request(req: dict) -> dict | None:
    req_id = req.get("id")
    method = req.get("method", "")
    params = req.get("params", {})

    # Notifications (no id) — no response needed
    if req_id is None:
        return None

    # MCP protocol methods
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "dim0-mcp", "version": "1.0.0"},
            },
        }

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": TOOLS},
        }

    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        handler = HANDLERS.get(tool_name)
        if handler is None:
            return _error_response(req_id, -32601, f"Unknown tool: {tool_name}")
        try:
            result = await handler(arguments)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(result, ensure_ascii=False, indent=2),
                        }
                    ],
                    "isError": False,
                },
            }
        except Exception as exc:
            logger.exception("Tool %s failed", tool_name)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"Error: {exc}"}],
                    "isError": True,
                },
            }

    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    return _error_response(req_id, -32601, f"Method not found: {method}")


async def run_server() -> None:
    """Main stdio server loop."""
    if not TOKEN:
        logger.error(
            "DIM0_INTEGRATION_TOKEN is not set. "
            "Set it in the MCP environment configuration."
        )
        sys.exit(1)

    logger.info("dim0-mcp server started. BASE_URL=%s", BASE_URL)
    logger.info("Default board: %s", DEFAULT_BOARD_ID or "(none — must pass board_id per call)")

    loop = asyncio.get_event_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)

    while True:
        try:
            line = await reader.readline()
            if not line:
                logger.info("stdin closed, exiting")
                break
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError as exc:
                _send(_error_response(None, -32700, f"Parse error: {exc}"))
                continue
            response = await _handle_request(req)
            if response is not None:
                _send(response)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.exception("Unexpected server error: %s", exc)


if __name__ == "__main__":
    asyncio.run(run_server())
