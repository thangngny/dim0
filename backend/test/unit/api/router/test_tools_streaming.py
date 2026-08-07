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


def _patch_convert(monkeypatch, attr_name: str) -> None:
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
def test_converters_stream_success_json(
    client, monkeypatch, path, convert_attr
) -> None:
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

    resp = client.post(
        "/tools/text:translate", json={"text": "hi", "target_language": "vi"}
    )

    assert resp.status_code == 200
    assert resp.headers["x-accel-buffering"] == "no"
    body = resp.json()
    assert body["status"] == "success"
    assert len(body["data"]["notes"]) == 1
    assert body["data"]["links"] == []
