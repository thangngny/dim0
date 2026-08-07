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
