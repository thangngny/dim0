"""Tests for the converter endpoints' streaming wiring."""
from unittest.mock import AsyncMock

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from topix.api.router.tools import router
from topix.api.utils.rate_limit.dependency import rate_limiter
from topix.api.utils.security import get_current_user_uid


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
