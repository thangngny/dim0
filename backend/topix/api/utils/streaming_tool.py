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

from collections.abc import AsyncGenerator, Callable, Coroutine
from typing import Any

from fastapi.responses import StreamingResponse

from topix.datatypes.note.link import Link
from topix.datatypes.note.note import Note

logger = logging.getLogger(__name__)

HEARTBEAT = b"\n"
DEFAULT_HEARTBEAT_INTERVAL = 5.0


async def _stream(
    run_fn: Callable[[], Coroutine[Any, Any, Any]],
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
                "links": [link.model_dump(exclude_none=True) for link in links],
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
    run_fn: Callable[[], Coroutine[Any, Any, Any]],
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
