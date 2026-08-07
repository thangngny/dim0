# Converter Heartbeat Streaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the 7 `/tools/*` converter endpoints from 504-ing through the public proxy by streaming newline keep-alive bytes while the agent runs, then emitting the final JSON.

**Architecture:** A shared helper `stream_tool_conversion(...)` returns a `StreamingResponse` that runs the agent as an `asyncio` task, yields `b"\n"` every 5 s until it finishes, then yields the final `{status,data}` JSON. nginx gets a `location /tools` block with buffering off + 3600 s timeout (mirroring `/integration/research/`). The Cloudflare Worker already streams `resp.body`; the frontend's `JSON.parse` ignores the leading whitespace, so no frontend change.

**Tech Stack:** FastAPI / Starlette `StreamingResponse`, `asyncio`, pytest + pytest-asyncio, nginx, Cloudflare Workers.

## Global Constraints

- Backend language: Python, FastAPI. Run `uv` for the env; `ruff check <file>` after editing `.py`; run `pytest` from `backend/`.
- Frontend style rules (project CLAUDE.md): TypeScript, no semicolons, named exports, no `any`. (This plan does NOT touch frontend, but keep in mind if a step strays.)
- Commit format: Conventional Commit with a mandatory specific scope (`tools`, `nginx`, `deploy`, etc.), short imperative lowercase, no trailing period. One logical change per commit.
- Do not change the converter agents, models, prompts, or the `:describe` endpoints.
- The converter response shape must stay `{"status":"success","data":{"notes":[...],"links":[...]}}` exactly (the frontend reads `res.data` then camelCases). On error: `{"status":"error","data":{"message":...}}` with HTTP 200 (matches existing `@with_standard_response`).
- Heartbeat byte is `b"\n"`; default interval 5.0 s. The helper must accept an `heartbeat_interval` kwarg so tests can use a tiny value.

## File Structure

- **Create:** `backend/topix/api/utils/streaming_tool.py` — the heartbeat-streaming helper (one responsibility: run an async agent, stream keep-alive + final JSON).
- **Create:** `backend/test/unit/api/utils/test_streaming_tool.py` — unit tests for the helper.
- **Modify:** `backend/topix/api/router/tools.py` — wire the 7 converter endpoints to the helper (drop `@with_standard_response` on them; keep auth + rate-limit deps).
- **Create:** `backend/test/unit/api/router/test_tools_streaming.py` — endpoint tests via `TestClient`.
- **Modify:** `deploy/aws/nginx/dim0.conf` — add `location /tools { ... }` mirroring `/integration/research/`.

---

## Task 1: `stream_tool_conversion` helper

**Files:**
- Create: `backend/topix/api/utils/streaming_tool.py`
- Test: `backend/test/unit/api/utils/test_streaming_tool.py`

**Interfaces:**
- Produces:
  - `stream_tool_conversion(run_fn, convert_fn, *, heartbeat_interval=5.0) -> fastapi.responses.StreamingResponse`
  - `run_fn: () -> Awaitable[Any]` — zero-arg async callable that runs the agent and returns its result.
  - `convert_fn: (agent_result) -> tuple[list[Note], list[Link]]` — turns the agent result into Note/Link objects (the helper serializes them via `model_dump(exclude_none=True)`).
  - The response has `media_type="application/json"` and header `X-Accel-Buffering: no`.

- [ ] **Step 1: Write the failing tests**

Create `backend/test/unit/api/utils/test_streaming_tool.py`:

```python
"""Unit tests for the converter heartbeat-streaming helper."""
import asyncio
import json

import pytest

from topix.api.utils.streaming_tool import _stream
from topix.datatypes.note.note import Note
from topix.datatypes.resource import RichText


def _note() -> Note:
    return Note(content=RichText(markdown="hello"))


@pytest.mark.asyncio
async def test_yields_heartbeat_then_success_json() -> None:
    """Heartbeats flow before the final success JSON."""
    async def run_fn():
        await asyncio.sleep(0.25)
        return "agent-result"

    def convert_fn(_result):
        return [_note()], []

    chunks = [c async for c in _stream(run_fn, convert_fn, heartbeat_interval=0.05)]

    heartbeat_count = sum(1 for c in chunks if c == b"\n")
    assert heartbeat_count >= 1, "at least one keep-alive newline before the result"
    payload = json.loads(chunks[-1])
    assert payload["status"] == "success"
    assert isinstance(payload["data"]["notes"], list)
    assert len(payload["data"]["notes"]) == 1
    assert payload["data"]["links"] == []


@pytest.mark.asyncio
async def test_fast_agent_emits_no_heartbeat_then_json() -> None:
    """When the agent resolves before the first interval, only JSON is emitted."""
    async def run_fn():
        return "agent-result"

    def convert_fn(_result):
        return [_note()], []

    chunks = [c async for c in _stream(run_fn, convert_fn, heartbeat_interval=0.05)]

    assert b"\n" not in b"".join(chunks)
    payload = json.loads(b"".join(chunks))
    assert payload["status"] == "success"


@pytest.mark.asyncio
async def test_agent_error_yields_error_json() -> None:
    """An agent failure yields the error JSON shape, not a 5xx."""

    async def run_fn():
        raise RuntimeError("boom")

    def convert_fn(_result):
        return [], []

    chunks = [c async for c in _stream(run_fn, convert_fn, heartbeat_interval=0.05)]

    payload = json.loads(b"".join(chunks))
    assert payload["status"] == "error"
    assert "message" in payload["data"]


@pytest.mark.asyncio
async def test_client_disconnect_cancels_task() -> None:
    """Closing the generator early cancels the in-flight agent task."""
    cancel_seen = asyncio.Event()

    async def run_fn():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancel_seen.set()
            raise

    def convert_fn(_result):
        return [], []

    gen = _stream(run_fn, convert_fn, heartbeat_interval=0.05)
    await gen.__anext__()  # consume one heartbeat; task still running
    await gen.aclose()

    await asyncio.wait_for(cancel_seen.wait(), timeout=1)
    assert cancel_seen.is_set()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest test/unit/api/utils/test_streaming_tool.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'topix.api.utils.streaming_tool'`.

- [ ] **Step 3: Write the helper implementation**

Create `backend/topix/api/utils/streaming_tool.py`:

```python
"""Heartbeat-streaming helper for long-running converter endpoints.

Converter endpoints run an LLM agent (~50 s, occasionally >100 s) and then
return a single JSON body. Through the public proxy (Cloudflare Worker ->
nginx) that intermittently 504s because no bytes flow until the agent
finishes. This helper streams a newline keep-alive every few seconds while
the agent runs, then emits the final JSON. Leading whitespace is ignored by
the frontend's JSON.parse, so consumers are unchanged.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any

from fastapi.responses import StreamingResponse

from topix.datatypes.note.link import Link
from topix.datatypes.note.note import Note

logger = logging.getLogger(__name__)

HEARTBEAT = b"\n"
DEFAULT_HEARTBEAT_INTERVAL = 5.0


async def _stream(
    run_fn: Callable[[], Awaitable[Any]],
    convert_fn: Callable[[Any], tuple[list[Note], list[Link]]],
    heartbeat_interval: float,
) -> AsyncGenerator[bytes, None]:
    """Yield keep-alive newlines while the agent runs, then the result JSON."""
    task = asyncio.create_task(run_fn())
    try:
        while not task.done():
            done, _ = await asyncio.wait({task}, timeout=heartbeat_interval)
            if not done:
                yield HEARTBEAT
        result = await task
        notes, links = convert_fn(result)
        payload = {
            "status": "success",
            "data": {
                "notes": [n.model_dump(exclude_none=True) for n in notes],
                "links": [l.model_dump(exclude_none=True) for l in links],
            },
        }
        yield json.dumps(payload).encode()
    except Exception as exc:  # noqa: BLE001 — best-effort conversion
        logger.warning("converter agent failed: %s", exc)
        yield json.dumps(
            {"status": "error", "data": {"message": "Conversion failed"}}
        ).encode()
    finally:
        if not task.done():
            task.cancel()


def stream_tool_conversion(
    run_fn: Callable[[], Awaitable[Any]],
    convert_fn: Callable[[Any], tuple[list[Note], list[Link]]],
    *,
    heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL,
) -> StreamingResponse:
    """Return a StreamingResponse that keep-alives while the agent runs.

    Args:
        run_fn: Zero-arg async callable that runs the agent and returns its result.
        convert_fn: Turns the agent result into a (notes, links) tuple of model
            objects; the helper serializes them with model_dump(exclude_none=True).
        heartbeat_interval: Seconds between keep-alive newlines (default 5s).
    """
    return StreamingResponse(
        _stream(run_fn, convert_fn, heartbeat_interval),
        media_type="application/json",
        headers={"X-Accel-Buffering": "no"},
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest test/unit/api/utils/test_streaming_tool.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Lint + commit**

Run: `cd backend && uv run ruff check topix/api/utils/streaming_tool.py test/unit/api/utils/test_streaming_tool.py`
Expected: no errors.

```bash
git add backend/topix/api/utils/streaming_tool.py backend/test/unit/api/utils/test_streaming_tool.py
git commit -m "feat(tools): add stream_tool_conversion heartbeat helper"
```

---

## Task 2: Wire the `mapify` endpoint to the helper

**Files:**
- Modify: `backend/topix/api/router/tools.py:53-72` (the `mapify` endpoint)
- Test: `backend/test/unit/api/router/test_tools_streaming.py`

**Interfaces:**
- Consumes: `stream_tool_conversion` from Task 1.
- Produces: `POST /tools/mindmaps:mapify` returns the same `{status,data}` JSON shape but as a streaming response with `X-Accel-Buffering: no`.

- [ ] **Step 1: Write the failing endpoint test**

Create `backend/test/unit/api/router/test_tools_streaming.py`:

```python
"""Tests for the converter endpoints' streaming wiring."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from topix.api.router.tools import router
from topix.api.utils.rate_limit.dependency import rate_limiter
from topix.api.utils.security import get_current_user_uid
from topix.datatypes.note.note import Note
from topix.datatypes.resource import RichText


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_uid] = lambda: "user-123"
    app.dependency_overrides[rate_limiter] = lambda: None
    with TestClient(app) as c:
        yield c


def test_mapify_streams_success_json(client, monkeypatch) -> None:
    from topix.agents.datatypes.outputs import MapifyTheme

    fake = MapifyTheme(label="Root", description="d", subthemes=[])
    monkeypatch.setattr(
        "topix.api.router.tools.AgentRunner.run",
        AsyncMock(return_value=fake),
    )

    resp = client.post("/tools/mindmaps:mapify", json={"answer": "x"})

    assert resp.status_code == 200
    assert resp.headers["x-accel-buffering"] == "no"
    body = resp.json()
    assert body["status"] == "success"
    assert "notes" in body["data"]
    assert "links" in body["data"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest test/unit/api/router/test_tools_streaming.py::test_mapify_streams_success_json -v`
Expected: FAIL — endpoint still returns via `@with_standard_response` (no `x-accel-buffering` header / wrong body shape is acceptable failure; the key is the assertion on header or that the response isn't streamed). The test will fail on `resp.headers["x-accel-buffering"]` KeyError.

- [ ] **Step 3: Wire the `mapify` endpoint**

In `backend/topix/api/router/tools.py`, add the import near the other top-level imports (after the `AgentRunner` import on line 14):

```python
from topix.api.utils.streaming_tool import stream_tool_conversion
```

Replace the `mapify` endpoint (lines 53–72) with:

```python
@router.post("/mindmaps:mapify/", include_in_schema=False)
@router.post("/mindmaps:mapify")
async def mapify(
    user_id: Annotated[str, Depends(get_current_user_uid)],
    body: Annotated[ConvertToMindMapRequest, Body(description="Mindmap conversion data")],
    _: Annotated[None, Depends(rate_limiter)],
):
    """Convert an answer into a mindmap graph (streamed with keep-alive)."""
    context = Context()
    mapify_agent = MapifyAgent()

    async def run_fn():
        return await AgentRunner.run(mapify_agent, body.answer, context=context)

    return stream_tool_conversion(run_fn, convert_mapify_output_to_notes_links)
```

Note: `@with_standard_response` is removed (the helper builds the `{status,data}` body itself). The `response: Response, request: Request` params are dropped — they were unused.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest test/unit/api/router/test_tools_streaming.py::test_mapify_streams_success_json -v`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

Run: `cd backend && uv run ruff check topix/api/router/tools.py test/unit/api/router/test_tools_streaming.py`
Expected: no errors.

```bash
git add backend/topix/api/router/tools.py backend/test/unit/api/router/test_tools_streaming.py
git commit -m "feat(tools): stream mapify endpoint with keep-alive"
```

---

## Task 3: Wire the remaining 6 converters

**Files:**
- Modify: `backend/topix/api/router/tools.py` — `notify` (31–50), `schemify` (75–94), `summify` (97–116), `quizify` (119–138), `drawify` (141–160), `translate` (177–199).
- Test: append to `backend/test/unit/api/router/test_tools_streaming.py`.

**Interfaces:**
- Consumes: `stream_tool_conversion` from Task 1.
- Produces: all 6 endpoints return the same streaming `{status,data}` shape.

- [ ] **Step 1: Write the failing parametrized test**

Append to `backend/test/unit/api/router/test_tools_streaming.py`:

```python
import pytest

from topix.agents.datatypes.outputs import MapifyTheme


def _patch_convert(monkeypatch, attr_name):
    """Patch a converter fn in the tools module to return one canned note."""
    monkeypatch.setattr(
        f"topix.api.router.tools.{attr_name}",
        lambda _res: ([Note(content=RichText(markdown="n"))], []),
    )


@pytest.mark.parametrize(
    ("path", "convert_attr"),
    [
        ("/tools/mindmaps:notify", "convert_notify_output_to_notes_links"),
        ("/tools/mindmaps:schemify", "convert_schemify_output_to_notes_links"),
        ("/tools/mindmaps:summify", "convert_schemify_output_to_notes_links"),
        ("/tools/mindmaps:quizify", "convert_schemify_output_to_notes_links"),
        ("/tools/drawify", "convert_drawify_output_to_notes_links"),
    ],
)
def test_converters_stream_success_json(client, monkeypatch, path, convert_attr) -> None:
    _patch_convert(monkeypatch, convert_attr)
    monkeypatch.setattr(
        "topix.api.router.tools.AgentRunner.run",
        AsyncMock(return_value=object()),
    )

    resp = client.post(path, json={"answer": "x"})

    assert resp.status_code == 200
    assert resp.headers["x-accel-buffering"] == "no"
    body = resp.json()
    assert body["status"] == "success"
    assert "notes" in body["data"] and "links" in body["data"]


def test_translate_streams_success_json(client, monkeypatch) -> None:
    """Translate builds a single note from res.text (no convert fn)."""
    monkeypatch.setattr(
        "topix.api.router.tools.AgentRunner.run",
        AsyncMock(return_value=SimpleNamespace(text="translated")),
    )

    resp = client.post("/tools/text:translate", json={"text": "hi", "target_language": "vi"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert len(body["data"]["notes"]) == 1
    assert body["data"]["links"] == []
```

Note: `summify` and `quizify` reuse `convert_schemify_output_to_notes_links` (as the current code does). The `translate` request body uses `text` + `target_language` (per `TranslateTextRequest`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest test/unit/api/router/test_tools_streaming.py -v`
Expected: the parametrized + translate tests FAIL (endpoints not yet wired; `x-accel-buffering` header missing).

- [ ] **Step 3: Wire the 6 endpoints**

In `backend/topix/api/router/tools.py`, replace each of the 6 endpoints the same way as `mapify` (drop `@with_standard_response` + the unused `response`/`request` params, delegate to `stream_tool_conversion`). The convert_fn for `translate` is inline.

`notify` (lines 31–50):
```python
@router.post("/mindmaps:notify/", include_in_schema=False)
@router.post("/mindmaps:notify")
async def notify(
    user_id: Annotated[str, Depends(get_current_user_uid)],
    body: Annotated[ConvertToMindMapRequest, Body(description="Mindmap conversion data")],
    _: Annotated[None, Depends(rate_limiter)],
):
    """Convert an answer into a notify graph (streamed with keep-alive)."""
    context = Context()
    notify_agent = NotifyAgent()

    async def run_fn():
        return await AgentRunner.run(notify_agent, body.answer, context=context)

    return stream_tool_conversion(run_fn, convert_notify_output_to_notes_links)
```

`schemify` (lines 75–94):
```python
@router.post("/mindmaps:schemify/", include_in_schema=False)
@router.post("/mindmaps:schemify")
async def schemify(
    user_id: Annotated[str, Depends(get_current_user_uid)],
    body: Annotated[ConvertToMindMapRequest, Body(description="Mindmap conversion data")],
    _: Annotated[None, Depends(rate_limiter)],
):
    """Convert an answer into a schema graph (streamed with keep-alive)."""
    context = Context()
    schemify_agent = SchemifyAgent()

    async def run_fn():
        return await AgentRunner.run(schemify_agent, body.answer, context=context)

    return stream_tool_conversion(run_fn, convert_schemify_output_to_notes_links)
```

`summify` (lines 97–116):
```python
@router.post("/mindmaps:summify/", include_in_schema=False)
@router.post("/mindmaps:summify")
async def summify(
    user_id: Annotated[str, Depends(get_current_user_uid)],
    body: Annotated[ConvertToMindMapRequest, Body(description="Mindmap conversion data")],
    _: Annotated[None, Depends(rate_limiter)],
):
    """Convert an answer into a summary graph (streamed with keep-alive)."""
    context = Context()
    summify_agent = SummifyAgent()

    async def run_fn():
        return await AgentRunner.run(summify_agent, body.answer, context=context)

    return stream_tool_conversion(run_fn, convert_schemify_output_to_notes_links)
```

`quizify` (lines 119–138):
```python
@router.post("/mindmaps:quizify/", include_in_schema=False)
@router.post("/mindmaps:quizify")
async def quizify(
    user_id: Annotated[str, Depends(get_current_user_uid)],
    body: Annotated[ConvertToMindMapRequest, Body(description="Mindmap conversion data")],
    _: Annotated[None, Depends(rate_limiter)],
):
    """Convert an answer into a quiz graph (streamed with keep-alive)."""
    context = Context()
    quizify_agent = QuizifyAgent()

    async def run_fn():
        return await AgentRunner.run(quizify_agent, body.answer, context=context)

    return stream_tool_conversion(run_fn, convert_schemify_output_to_notes_links)
```

`drawify` (lines 141–160):
```python
@router.post("/drawify/", include_in_schema=False)
@router.post("/drawify")
async def drawify(
    user_id: Annotated[str, Depends(get_current_user_uid)],
    body: Annotated[ConvertToMindMapRequest, Body(description="Drawify conversion data")],
    _: Annotated[None, Depends(rate_limiter)],
):
    """Convert a text prompt into a drawn diagram graph (streamed with keep-alive)."""
    context = Context()
    drawify_agent = DrawifyAgent()

    async def run_fn():
        return await AgentRunner.run(drawify_agent, body.answer, context=context)

    return stream_tool_conversion(run_fn, convert_drawify_output_to_notes_links)
```

`translate` (lines 177–199) — note the request body uses `text` and `target_language`, and the convert_fn builds a single note from `res.text`:
```python
@router.post("/text:translate/", include_in_schema=False)
@router.post("/text:translate")
async def translate_text(
    user_id: Annotated[str, Depends(get_current_user_uid)],
    body: Annotated[TranslateTextRequest, Body(description="Text translation data")],
    _: Annotated[None, Depends(rate_limiter)],
):
    """Translate text into the target language (streamed with keep-alive)."""
    context = Context()
    translate_agent = TranslateAgent(target_language=body.target_language)

    async def run_fn():
        return await AgentRunner.run(translate_agent, body.text, context=context)

    def convert_fn(res):
        return (
            [Note(label=RichText(markdown=res.text))],
            [],
        )

    return stream_tool_conversion(run_fn, convert_fn)
```

After edits, check that the now-unused imports (`Body`, `Response`, `Request`, `with_standard_response`) are still needed: `Body` is still used by the remaining endpoints; `Response`/`Request` are now unused if no endpoint uses them — remove them from the import line `from fastapi import APIRouter, Body, Depends, Request, Response` → `from fastapi import APIRouter, Body, Depends`. `with_standard_response` is now unused (all converted endpoints dropped it) — remove `from topix.api.utils.decorators import with_standard_response`. Verify with `ruff`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest test/unit/api/router/test_tools_streaming.py -v`
Expected: PASS (mapify + 5 parametrized + translate = 7 endpoint tests).

- [ ] **Step 5: Run the broader router test suite to catch regressions**

Run: `cd backend && uv run pytest test/unit/api/ -v -x`
Expected: PASS (no regressions in other api tests).

- [ ] **Step 6: Lint + commit**

Run: `cd backend && uv run ruff check topix/api/router/tools.py test/unit/api/router/test_tools_streaming.py`
Expected: no errors (fix any unused-import warnings ruff reports).

```bash
git add backend/topix/api/router/tools.py backend/test/unit/api/router/test_tools_streaming.py
git commit -m "feat(tools): stream all converter endpoints with keep-alive"
```

---

## Task 4: nginx `location /tools` (buffering off, long timeout)

**Files:**
- Modify: `deploy/aws/nginx/dim0.conf` — add a `location /tools { ... }` block mirroring `/integration/research/`.

**Interfaces:**
- Produces: nginx passes `/tools/*` responses through unbuffered with a 3600 s read/send timeout, so heartbeat bytes reach the Cloudflare Worker immediately.

- [ ] **Step 1: Edit the nginx template**

In `deploy/aws/nginx/dim0.conf`, add a new block after the `/integration/research/` block (before the closing `}` of the `server` block):

```nginx
    # Converter endpoints (/tools/*): long-running LLM agents that stream
    # keep-alive newlines then a final JSON body. Disable buffering so the
    # heartbeats reach the Cloudflare Worker immediately (else the edge
    # 504s before the first byte). Same shape as /integration/research/.
    location /tools {
        proxy_pass http://127.0.0.1:__BACKEND_PORT__;
        proxy_http_version 1.1;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
```

- [ ] **Step 2: Apply to the live instance + validate**

The instance runs the substituted config at `/etc/nginx/sites-available/dim0` (symlinked into `sites-enabled`). Apply the same `location /tools` block there with the real backend port (8080), then validate + reload:

```bash
ssh dim0-ssm 'PORT=$(ss -tlnp | grep -oE ":80[0-9]{3}" | head -1 | tr -d :); \
  sudo sed -i "/location \/integration\/research\//i\\
    location /tools {\n\
        proxy_pass http://127.0.0.1:${PORT:-8080};\n\
        proxy_http_version 1.1;\n\
        proxy_buffering off;\n\
        proxy_cache off;\n\
        proxy_read_timeout 3600s;\n\
        proxy_send_timeout 3600s;\n\
        proxy_set_header Host \$host;\n\
        proxy_set_header X-Real-IP \$remote_addr;\n\
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;\n\
        proxy_set_header X-Forwarded-Proto \$scheme;\n\
    }" /etc/nginx/sites-available/dim0 && \
  sudo nginx -t && sudo systemctl reload nginx && echo NGINX_OK'
```

Expected: prints `nginx: configuration file ... test is successful` then `NGINX_OK`. (If a `location /tools` block already exists from a prior run, remove the duplicate before re-running.)

- [ ] **Step 3: Verify the live config has the block**

Run: `ssh dim0-ssm 'sudo grep -A2 "location /tools" /etc/nginx/sites-available/dim0'`
Expected: shows `location /tools {` with `proxy_buffering off`.

- [ ] **Step 4: Commit the template change**

```bash
git add deploy/aws/nginx/dim0.conf
git commit -m "fix(nginx): unbuffer /tools for streaming converter endpoints"
```

---

## Task 5: Deploy backend + end-to-end verification

**Files:**
- No new files. Deploy the Task 1–3 backend changes to the instance and verify through the public proxy.

- [ ] **Step 1: Push the branch to the fork**

```bash
git push origin main
```

Expected: 3 commits (helper, mapify, all converters) + nginx commit pushed to `thangngny/dim0`.

- [ ] **Step 2: Pull + rsync on the instance + restart backend**

```bash
ssh dim0-ssm 'cd ~/dim0 && git pull && \
  sudo rsync -av --files-from=- ~/dim0/ /opt/dim0/backend/ <<EOF
topix/api/utils/streaming_tool.py
topix/api/router/tools.py
EOF
  sudo systemctl restart dim0-backend && sleep 2 && systemctl is-active dim0-backend'
```

Expected: `active`. (rsync `--files-from=-` copies only the two changed/new files into `/opt/dim0/backend/`.)

- [ ] **Step 3: Verify on the origin — heartbeat + final JSON, 200**

```bash
JWT="<use the e2e-tester JWT from the prior session, or re-login to get one>"
BODY='{"answer":"- **Lower latency** — near-source processing cuts round-trip time.\n- **Reduced bandwidth** — less traffic to the cloud.\n- **Resilience** — edge nodes run without cloud."}'
ssh dim0-ssm "curl -sN -m 120 -X POST http://localhost:8080/tools/mindmaps:mapify -H 'Authorization: Bearer $JWT' -H 'Content-Type: application/json' -d '$BODY' | head -c 40"
```

Expected: output begins with one or more newlines (`\n`) then `{"status":"success","data":{"notes":...`. The newlines prove the heartbeat streamed before the result.

- [ ] **Step 4: Verify through the public proxy — 200, not 504**

```bash
curl -s -m 120 -o /tmp/pub_mapify.txt -w 'http=%{http_code} time=%{time_total}s\n' \
  -X POST https://dim0-proxy.dim0-thang.workers.dev/tools/mindmaps:mapify \
  -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" -d "$BODY"
head -c 120 /tmp/pub_mapify.txt
```

Expected: `http=200` (NOT 504), body is valid `{"status":"success",...}` JSON. (Run a couple of times — the point is it no longer 504s when the agent is slow.)

- [ ] **Step 5: Browser e2e — convert answer to mind map**

In the existing browser session (logged in as `e2e-tester@dim0test.com`):
- Open the chat with the edge-computing answer.
- Click "Convert current answer to a mind map" → "Create New Board".
- Wait for the board to populate with nodes (no 504 toast).

Take a screenshot: `mcp__chrome-devtools-mcp__take_screenshot` to `/tmp/dim0-e2e-mapify-fixed.png`.
Expected: board shows the mind-map nodes; the sidebar board entry has a real title (or "Untitled" if the board `:describe` hasn't fired yet — acceptable); no 504 toast.

- [ ] **Step 6: Check backend logs for errors during the test**

```bash
ssh dim0-ssm 'sudo journalctl -u dim0-backend --since "10 min ago" --no-pager | grep -iE "error|traceback|exception|converter agent failed" | tail -20'
```

Expected: empty (no errors), or only `converter agent failed` warnings if a model glitch happened (which is the resilient path working, not a crash).

- [ ] **Step 7: Final commit note (nothing to commit) — summarize**

No further code changes. Report: helper + 7 endpoints streaming, nginx `/tools` unbuffered, origin + public proxy both return 200, browser e2e passes.

---

## Self-Review (already run)

- **Spec coverage:** Helper (Task 1) ✓; wire 7 converters (Tasks 2–3) ✓; nginx (Task 4) ✓; deploy + verify origin/public/browser (Task 5) ✓; error + disconnect paths tested (Task 1) ✓. `:describe` explicitly out of scope ✓.
- **Placeholder scan:** No TBD/TODO. All code blocks are concrete. The JWT in Task 5 Step 3–4 is a runtime value (re-obtain), not a placeholder.
- **Type consistency:** `stream_tool_conversion(run_fn, convert_fn, *, heartbeat_interval=5.0)` used identically in Tasks 1–3. `convert_fn` returns `tuple[list[Note], list[Link]]` everywhere. `run_fn` is a zero-arg async callable everywhere.