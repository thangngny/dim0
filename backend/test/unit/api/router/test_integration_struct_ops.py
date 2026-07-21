"""Router tests for the five structural-op integration endpoints.

Exercises auth (401 without token) and the merge preview shape. Uses a
real `AgentBoardBridge` wired to a stub `GraphStore` that implements just
enough of the surface (`get_nodes`, `get_graph`) for the merge preview
path — the endpoint truly exercises the bridge, not a canned fake.
"""

import pytest
import httpx
from fastapi import FastAPI
from httpx import ASGITransport

from topix.api.router.integration import router
from topix.collab.agent_bridge import AgentBoardBridge
from topix.collab.room import RoomRegistry
from topix.datatypes.graph.graph import Graph
from topix.datatypes.note.note import Note
from topix.datatypes.resource import RichText


class _StubGraphStore:
    """Minimal GraphStore backing the bridge methods these tests exercise.

    Only `get_nodes` and `get_graph` are consulted on the merge preview
    path (confirm=False). Other methods are intentionally absent so any
    drift onto a mutating path fails loudly.
    """

    def __init__(self, notes: list[Note], graph: Graph | None = None):
        self._notes_by_id = {n.id: n for n in notes}
        self._graph = graph

    async def get_nodes(self, node_ids: list[str]) -> list[Note]:
        return [self._notes_by_id[nid] for nid in node_ids if nid in self._notes_by_id]

    async def get_graph(self, graph_uid: str, root_id: str | None = None) -> Graph | None:
        return self._graph


@pytest.fixture
def board_id() -> str:
    """Stable board id shared across fixtures in a single test."""
    return "board-test"


@pytest.fixture
def integration_token(monkeypatch) -> str:
    """Set the integration token env var; tests read it via the header."""
    monkeypatch.setenv("DIM0_INTEGRATION_TOKEN", "test-token")
    return "test-token"


@pytest.fixture
def notes(board_id) -> list[Note]:
    """Two notes that live on `board_id`; ids are shared with `two_notes`."""
    return [
        Note(
            graph_uid=board_id,
            label=RichText(markdown="A"),
            content=RichText(markdown="alpha"),
        ),
        Note(
            graph_uid=board_id,
            label=RichText(markdown="B"),
            content=RichText(markdown="beta"),
        ),
    ]


@pytest.fixture
def two_notes(notes) -> tuple[str, str]:
    """Return the two note ids for the test's request bodies."""
    return notes[0].id, notes[1].id


@pytest.fixture
async def async_client(integration_token, board_id, notes):
    """ASGI client with the integration router and a real bridge wired to a stub store."""
    graph = Graph(uid=board_id, nodes=notes, edges=[])
    graph_store = _StubGraphStore(notes=notes, graph=graph)
    app = FastAPI()
    app.include_router(router)
    app.graph_store = graph_store
    app.agent_board_bridge = AgentBoardBridge(
        graph_store=graph_store, registry=RoomRegistry(),
    )
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_set_kind_endpoint_requires_token(async_client, board_id, integration_token):
    """No X-Integration-Token header → 401, even with the env var configured."""
    r = await async_client.post(
        f"/integration/boards/{board_id}/nodes/none:set-kind",
        json={"kind": "finding"},
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_merge_endpoint_preview(async_client, board_id, integration_token, two_notes):
    """confirm=False returns a preview with the absorbed (non-target) count."""
    a, b = two_notes
    r = await async_client.post(
        f"/integration/boards/{board_id}/nodes:merge",
        headers={"X-Integration-Token": integration_token},
        json={"node_ids": [a, b], "target_id": a, "confirm": False},
    )
    assert r.status_code == 200
    assert r.json()["preview"]["absorbed"] == 1