"""Integration tests for the GraphStore class."""

import pytest
import pytest_asyncio

from topix.datatypes.graph.graph import Graph
from topix.datatypes.note.link import Link
from topix.datatypes.note.note import Note
from topix.datatypes.property import PositionProperty
from topix.datatypes.resource import RichText
from topix.datatypes.user import User
from topix.store.graph import GraphStore
from topix.store.postgres.graph import get_graph_id_by_uid
from topix.store.postgres.graph_user import add_user_to_graph_by_uid
from topix.store.qdrant.store import ContentStore
from topix.store.user import UserStore
from topix.utils.common import gen_uid


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def init_collection():
    """Initialize the Qdrant collection for graph tests."""
    await ContentStore.from_config().create_collection(force_recreate=True)


@pytest.mark.asyncio
async def test_graph_crud_lifecycle(config, init_collection):
    """Test the CRUD lifecycle of a graph."""
    store = GraphStore()
    await store.open()
    user_uid = "root"
    try:
        # 1. Create graph
        graph = Graph(label="Test Graph")
        await store.add_graph(graph, user_uid=user_uid)

        # 2. Fetch graph and assert fields
        stored_graph = await store.get_graph(graph.uid)
        assert stored_graph is not None
        assert stored_graph.uid == graph.uid
        assert stored_graph.label == "Test Graph"
        assert stored_graph.nodes == []
        assert stored_graph.edges == []

        # 3. Add nodes
        node1 = Note(
            label=RichText(markdown="First Node"),
            graph_uid=graph.uid,
            content=RichText(markdown="# Hello"),
        )
        node2 = Note(
            label=RichText(markdown="Second Node"),
            graph_uid=graph.uid,
            content=RichText(markdown="World!"),
        )
        await store.add_notes([node1, node2])

        # 4. Fetch nodes and verify
        nodes = await store.get_nodes([node1.id, node2.id])
        assert {n.id for n in nodes} == {node1.id, node2.id}

        # 5. Add link between nodes
        link = Link(
            source=node1.id,
            target=node2.id,
            graph_uid=graph.uid,
            content=RichText(markdown="Friend")
        )
        await store.add_links([link])

        # 6. Fetch links and verify
        links = await store.get_links([link.id])
        assert links[0].source == node1.id
        assert links[0].target == node2.id

        # 7. Fetch whole graph, check nodes and edges
        graph_with_data = await store.get_graph(graph.uid)
        assert any(n.id == node1.id for n in graph_with_data.nodes)
        assert any(e.id == link.id for e in graph_with_data.edges)

        # 8. Update a node
        await store.update_node(node1.id, {"type": "note", "label": {"markdown": "First Node Updated"}})
        updated_nodes = await store.get_nodes([node1.id])
        assert updated_nodes[0].label.markdown == "First Node Updated"

        # 9. Delete a node
        await store.delete_node(node2.id, hard_delete=False)
        remaining_nodes = await store.get_nodes([node1.id, node2.id])
        assert len(remaining_nodes) == 2
        for node in remaining_nodes:
            if node.deleted_at is not None:
                assert node.id == node2.id
            else:
                assert node.id == node1.id

        # 10. Delete graph (soft)
        await store.delete_graph(graph.uid, hard_delete=False)
        deleted_graph = await store.get_graph(graph.uid)
        assert deleted_graph is not None
        assert deleted_graph.deleted_at is not None

        # 11. Hard delete graph
        await store.delete_graph(graph.uid, hard_delete=True)
        hard_deleted_graph = await store.get_graph(graph.uid)
        assert hard_deleted_graph is None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_graph_role_lookup(config, init_collection):
    """Test GraphStore role lookup for owner/member/non-member."""
    graph_store = GraphStore()
    user_store = UserStore()
    await graph_store.open()
    await user_store.open()

    owner_uid = "root"
    member_uid = gen_uid()

    try:
        graph = Graph(label="Role Lookup Graph")
        await graph_store.add_graph(graph, user_uid=owner_uid)

        owner_role = await graph_store.get_graph_role(graph.uid, owner_uid)
        assert owner_role == "owner"

        member = User(
            uid=member_uid,
            email=f"{member_uid}@test.com",
            username=member_uid,
            password_hash="hashed_password",
        )
        await user_store.add_user(member)

        async with graph_store._pg_pool.acquire() as conn:
            added = await add_user_to_graph_by_uid(conn, graph.uid, member_uid, "member")
        assert added is True

        member_role = await graph_store.get_graph_role(graph.uid, member_uid)
        assert member_role == "member"

        no_role = await graph_store.get_graph_role(graph.uid, gen_uid())
        assert no_role is None
    finally:
        await graph_store.delete_graph(graph.uid, hard_delete=True)
        await user_store.delete_user(member_uid, hard_delete=True)
        await graph_store.close()
        await user_store.close()


@pytest.mark.asyncio
async def test_add_graph_atomic_owner_assignment(config, init_collection):
    """add_graph must commit graph + owner row atomically.

    Regression: create_graph and add_user_to_graph_by_uid used to run as
    two autocommits, leaving a window where a private board existed with
    no owner row → GET /boards/{id} returned 404 intermittently (the
    role check found no owner, visibility was private). If owner
    assignment fails, the graph insert must roll back so no board ever
    exists without an owner.
    """
    graph_store = GraphStore()
    await graph_store.open()
    bogus_user_uid = gen_uid()  # not in users table → ValueError on assign
    try:
        graph = Graph(label="Atomicity Graph")
        with pytest.raises(ValueError):
            await graph_store.add_graph(graph, user_uid=bogus_user_uid)

        async with graph_store._pg_pool.acquire() as conn:
            graph_id = await get_graph_id_by_uid(conn, graph.uid)
        assert graph_id is None, "graph row must roll back when owner assign fails"
    finally:
        await graph_store.close()


@pytest.mark.asyncio
async def test_graph_visibility_defaults_and_updates(config, init_collection):
    """Test graph visibility defaults to private and can be updated."""
    store = GraphStore()
    await store.open()
    user_uid = "root"

    try:
        graph = Graph(label="Visibility Graph")
        await store.add_graph(graph, user_uid=user_uid)

        stored_graph = await store.get_graph(graph.uid)
        assert stored_graph is not None
        assert stored_graph.visibility == "private"

        await store.update_graph(graph.uid, {"visibility": "public"})

        updated_graph = await store.get_graph(graph.uid)
        assert updated_graph is not None
        assert updated_graph.visibility == "public"
    finally:
        await store.delete_graph(graph.uid, hard_delete=True)
        await store.close()


@pytest.mark.asyncio
async def test_graph_keeps_links_with_free_endpoints(config, init_collection):
    """Test graph loading keeps links that rely on persisted free endpoints."""
    store = GraphStore()
    await store.open()
    user_uid = "root"

    try:
        graph = Graph(label="Free Endpoint Graph")
        await store.add_graph(graph, user_uid=user_uid)

        node = Note(
            label=RichText(markdown="Anchored Node"),
            graph_uid=graph.uid,
        )
        await store.add_notes([node])

        floating_link = Link(
            source="floating-start",
            target="floating-end",
            graph_uid=graph.uid,
            properties={
                "start_point": PositionProperty(
                    position=PositionProperty.Position(x=10, y=20),
                ),
                "end_point": PositionProperty(
                    position=PositionProperty.Position(x=120, y=160),
                ),
            },
        )
        partial_link = Link(
            source=node.id,
            target="floating-target",
            graph_uid=graph.uid,
            properties={
                "end_point": PositionProperty(
                    position=PositionProperty.Position(x=240, y=280),
                ),
            },
        )
        await store.add_links([floating_link, partial_link])

        graph_with_data = await store.get_graph(graph.uid)

        assert {edge.id for edge in graph_with_data.edges} >= {
            floating_link.id,
            partial_link.id,
        }
    finally:
        await store.delete_graph(graph.uid, hard_delete=True)
        await store.close()


@pytest.mark.asyncio
async def test_update_link_merges_partial_payload(config, init_collection):
    """Test link updates merge into the stored link instead of replacing it."""
    store = GraphStore()
    await store.open()
    user_uid = "root"

    try:
        graph = Graph(label="Update Link Graph")
        await store.add_graph(graph, user_uid=user_uid)

        node1 = Note(
            label=RichText(markdown="Node 1"),
            graph_uid=graph.uid,
        )
        node2 = Note(
            label=RichText(markdown="Node 2"),
            graph_uid=graph.uid,
        )
        await store.add_notes([node1, node2])

        link = Link(
            source=node1.id,
            target=node2.id,
            graph_uid=graph.uid,
            properties={
                "start_point": PositionProperty(
                    position=PositionProperty.Position(x=30, y=40),
                ),
                "end_point": PositionProperty(
                    position=PositionProperty.Position(x=130, y=140),
                ),
            },
        )
        await store.add_links([link])

        await store.update_link(
            link_id=link.id,
            data={
                "parent_id": node1.id,
            },
        )

        updated_link = (await store.get_links([link.id]))[0]
        assert updated_link.parent_id == node1.id
        assert updated_link.source == node1.id
        assert updated_link.target == node2.id
        assert updated_link.graph_uid == graph.uid
        assert updated_link.properties.start_point is not None
        assert updated_link.properties.end_point is not None
    finally:
        await store.delete_graph(graph.uid, hard_delete=True)
        await store.close()


@pytest.mark.asyncio
async def test_graph_filters_links_by_parent_scope(config, init_collection):
    """Test graph loading scopes links by their parent_id in nested boards."""
    store = GraphStore()
    await store.open()
    user_uid = "root"

    try:
        graph = Graph(label="Scoped Link Graph")
        await store.add_graph(graph, user_uid=user_uid)

        folder = Note(
            label=RichText(markdown="Folder"),
            graph_uid=graph.uid,
        )
        child = Note(
            label=RichText(markdown="Child"),
            graph_uid=graph.uid,
            parent_id=folder.id,
        )
        await store.add_notes([folder, child])

        root_link = Link(
            source="root-start",
            target="root-end",
            graph_uid=graph.uid,
            properties={
                "start_point": PositionProperty(
                    position=PositionProperty.Position(x=20, y=30),
                ),
                "end_point": PositionProperty(
                    position=PositionProperty.Position(x=80, y=90),
                ),
            },
        )
        nested_link = Link(
            source=child.id,
            target="nested-end",
            graph_uid=graph.uid,
            parent_id=folder.id,
            properties={
                "end_point": PositionProperty(
                    position=PositionProperty.Position(x=180, y=210),
                ),
            },
        )
        await store.add_links([root_link, nested_link])

        root_graph = await store.get_graph(graph.uid)
        nested_graph = await store.get_graph(graph.uid, root_id=folder.id)

        assert {edge.id for edge in root_graph.edges} == {root_link.id}
        assert {edge.id for edge in nested_graph.edges} == {nested_link.id}
    finally:
        await store.delete_graph(graph.uid, hard_delete=True)
        await store.close()
