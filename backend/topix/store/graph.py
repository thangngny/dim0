"""GraphStore for managing graph data in the database."""

import asyncio
import logging

import asyncpg

from qdrant_client.models import (
    FieldCondition,
    Filter,
    MatchAny,
    MatchValue,
)

from topix.datatypes.file.document import Document
from topix.datatypes.graph.graph import Graph
from topix.datatypes.note.link import Link
from topix.datatypes.note.note import Note
from topix.store.note_revision import NoteRevisionStore, deserialize_note_snapshot
from topix.store.postgres.graph import (
    _dangerous_hard_delete_graph_by_uid,
    create_graph,
    delete_graph_by_uid,
    get_graph_by_uid,
    update_graph_by_uid,
)
from topix.store.postgres.graph_user import (
    add_user_to_graph_by_uid,
    get_graph_role_by_user_uid,
    get_owner_uid_by_graph_uid,
    list_graphs_by_user_uid,
    list_members_for_graph,
    remove_user_from_graph_by_uid,
)
from topix.store.postgres.pool import create_pool
from topix.store.qdrant.store import ContentStore

DEFAULT_SNAPSHOT_CONCURRENCY = 8

logger = logging.getLogger(__name__)


class GraphStore:
    """Store for managing graph data in the database."""

    def __init__(self, snapshot_concurrency: int = DEFAULT_SNAPSHOT_CONCURRENCY):
        """Initialize the GraphStore.

        ``snapshot_concurrency`` caps how many background note-snapshot writers
        can hold a Postgres connection at once. This is the main backpressure
        guard against bursty edits exhausting the shared pool.
        """
        self._content_store = ContentStore.from_config()
        self._pg_pool: asyncpg.Pool | None = None
        self._owns_pool = False
        self._note_revision_store: NoteRevisionStore | None = None
        self._note_locks: dict[str, asyncio.Lock] = {}
        self._snapshot_sem = asyncio.Semaphore(snapshot_concurrency)
        self._snapshot_tasks: set[asyncio.Task] = set()

    def note_lock(self, note_id: str) -> asyncio.Lock:
        """Return a per-note lock used to serialize tool-level read-modify-write edits."""
        return self._note_locks.setdefault(note_id, asyncio.Lock())

    async def open(self, pool: asyncpg.Pool | None = None):
        """Open the store. Pass a shared pool, or omit to create a private one."""
        if pool is None:
            self._pg_pool = await create_pool()
            self._owns_pool = True
        else:
            self._pg_pool = pool
            self._owns_pool = False
        self._note_revision_store = NoteRevisionStore(self._pg_pool)
        await self._note_revision_store.ensure_table()

    async def add_notes(self, nodes: list[Note]):
        """Add nodes to the graph."""
        # TODO(folder): validate parent_id exists and belongs to the same graph.
        # TODO(folder): reject invalid parent assignments and cycles on create.
        await self._content_store.add(nodes)

    @staticmethod
    def _link_is_visible_in_scope(
        link: Link,
        root_id: str | None,
    ) -> bool:
        """Return whether a link belongs to the requested board scope."""
        if root_id is None:
            return link.parent_id is None

        return link.parent_id == root_id

    def _schedule_note_snapshot(self, note: Note, user_uid: str | None = None) -> None:
        """Persist a note snapshot in the background when revision storage is enabled."""
        if self._note_revision_store is None:
            return

        note_to_snapshot = note.model_copy(deep=True)
        revision_store = self._note_revision_store
        sem = self._snapshot_sem

        def _log_task_result(task: asyncio.Task) -> None:
            self._snapshot_tasks.discard(task)
            try:
                task.result()
            except Exception as e:
                logger.exception("Background save_note_snapshot failed", exc_info=e)

        async def _bounded_save_snapshot() -> None:
            async with sem:
                await revision_store.save_note_snapshot(note_to_snapshot, user_uid=user_uid)

        task = asyncio.create_task(_bounded_save_snapshot())
        self._snapshot_tasks.add(task)
        task.add_done_callback(_log_task_result)

    @staticmethod
    def _deep_merge_dict(base: dict, patch: dict) -> dict:
        """Recursively merge a patch dict into an existing payload dict."""
        merged = dict(base)
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = GraphStore._deep_merge_dict(merged[key], value)
            else:
                merged[key] = value
        return merged

    async def patch_note(self, node_id: str, data: dict, user_uid: str | None = None) -> Note | None:
        """Patch a note by merging the update into the full stored note payload.

        Validates the merged payload against the matching pydantic model so
        documents (subclass of Note with `type: Literal["document"]`) keep
        their type discriminator and document-specific properties intact —
        otherwise validating a document row against the bare `Note` model
        fails with a literal_error on the `type` field.

        **Embed-skip fast path.** When the patch leaves every embeddable
        field unchanged (label / content / searchable TextProperties),
        the embedder is bypassed and only the Qdrant payload is updated.
        This keeps spatial ops (drag, resize, z-order, color) off the
        OpenAI hot path — critical because the collab apply lock is
        held across this call.
        """
        existing_nodes = await self.get_nodes([node_id])
        if not existing_nodes:
            return None

        existing_note = existing_nodes[0]
        self._schedule_note_snapshot(existing_note, user_uid=user_uid)

        merged_payload = self._deep_merge_dict(
            existing_note.model_dump(exclude_none=False),
            data,
        )
        merged_payload["id"] = node_id
        model = Document if isinstance(existing_note, Document) else Note
        merged_note = model.model_validate(merged_payload)

        if merged_note.to_embeddable() == existing_note.to_embeddable():
            await self._content_store.update_payload_only(
                [merged_note.model_dump(exclude_none=False)]
            )
        else:
            await self._content_store.update(
                [merged_note.model_dump(exclude_none=False)]
            )
        return merged_note

    async def patch_notes(
        self, updates: list[tuple[str, dict]], user_uid: str | None = None,
    ) -> list[Note]:
        """Bulk-patch multiple notes in one DB round-trip.

        Semantics match `patch_note` per-row (validate against `Note` or
        `Document`, embed-skip fast path, snapshot the pre-image), but
        the work is amortized:

          - **One** `get_nodes(ids)` for the full id set.
          - In-memory merges per row.
          - **One** `update_payload_only(...)` for the embed-skip rows
            and **one** `update(...)` for the embed-required rows
            (instead of N separate writes).

        For a batch of 1000 user-coloured node.updates, this collapses
        2000+ DB calls (N reads + N writes) into 2 calls. Snapshot writes
        stay fire-and-forget via `_schedule_note_snapshot`.

        Same-id duplicates within `updates` are applied in input order;
        each merge sees the result of the previous one. Used by the
        collab apply path when a coalesced batch carries multiple
        updates targeting the same node id.

        Returns the merged Notes in input order (skipping rows whose
        target id wasn't found, which would correspond to a peer
        racing a delete).
        """
        if not updates:
            return []

        # Single bulk read of every targeted id (preserves the input id
        # order in a dict for deterministic merging).
        unique_ids = list(dict.fromkeys(node_id for node_id, _ in updates))
        existing_notes = await self.get_nodes(unique_ids)
        by_id: dict[str, Note] = {n.id: n for n in existing_notes}

        results: list[Note] = []
        embed_skip_payloads: list[dict] = []
        embed_required_payloads: list[dict] = []

        for node_id, data in updates:
            cur = by_id.get(node_id)
            if cur is None:
                continue
            self._schedule_note_snapshot(cur, user_uid=user_uid)
            merged_payload = self._deep_merge_dict(
                cur.model_dump(exclude_none=False),
                data,
            )
            merged_payload["id"] = node_id
            model = Document if isinstance(cur, Document) else Note
            merged_note = model.model_validate(merged_payload)
            # Subsequent same-id updates in this batch see the result of
            # this one — match per-op semantics for repeated patches.
            by_id[node_id] = merged_note

            dump = merged_note.model_dump(exclude_none=False)
            if merged_note.to_embeddable() == cur.to_embeddable():
                embed_skip_payloads.append(dump)
            else:
                embed_required_payloads.append(dump)
            results.append(merged_note)

        if embed_skip_payloads:
            await self._content_store.update_payload_only(embed_skip_payloads)
        if embed_required_payloads:
            await self._content_store.update(embed_required_payloads)
        return results

    async def update_node(self, node_id: str, data: dict, user_uid: str | None = None):
        """Update a node in the graph."""
        # TODO(folder): validate parent_id exists and belongs to the same graph.
        # TODO(folder): reject self-parent and cyclic reparent operations.
        existing_nodes = await self.get_nodes([node_id])
        if existing_nodes and self._note_revision_store is not None:
            self._schedule_note_snapshot(existing_nodes[0], user_uid=user_uid)
        data["id"] = node_id
        await self._content_store.update([data])

    async def delete_node(self, node_id: str, hard_delete: bool = True, user_uid: str | None = None):
        """Delete a node and every descendant linked via parent_id.

        Saves a snapshot for each deleted note so restore-latest still works
        per node, then issues a single batched content-store delete and a
        single chunks delete keyed on the full set of ids.
        """
        existing_nodes = await self.get_nodes([node_id])
        if not existing_nodes:
            return

        descendants = await self.get_node_descendants(node_id)
        all_to_delete: list[Note] = list(existing_nodes) + list(descendants)
        all_ids = [n.id for n in all_to_delete]

        if self._note_revision_store is not None:
            for n in all_to_delete:
                try:
                    await self._note_revision_store.save_note_snapshot(n, user_uid=user_uid)
                except Exception as e:
                    logger.exception("Failed to snapshot note %s before delete", n.id, exc_info=e)

        await self._content_store.delete(all_ids, hard_delete=hard_delete)

        # Cascade-delete links whose source or target was removed, mirroring
        # delete_subtree — otherwise get_graph returns dangling edges that
        # point at the deleted nodes and accumulate forever.
        board_id = existing_nodes[0].graph_uid
        orphan_link_results = await self._content_store.filt(
            filters=Filter(
                must=[
                    FieldCondition(key="graph_uid", match=MatchValue(value=board_id)),
                    FieldCondition(key="type", match=MatchValue(value="link")),
                ],
                should=[
                    FieldCondition(key="source", match=MatchAny(any=all_ids)),
                    FieldCondition(key="target", match=MatchAny(any=all_ids)),
                ],
            )
        )
        orphan_link_ids = [
            result.id for result in orphan_link_results
            if isinstance(result.resource, Link)
        ]
        if orphan_link_ids:
            await self._content_store.delete(orphan_link_ids, hard_delete=hard_delete)

        # deleted associated chunks for every removed node in one filter
        def _log_task_result(task: asyncio.Task) -> None:
            try:
                task.result()
            except Exception as e:
                logger.exception("Background delete_by_filters to delete associated chunks failed", exc_info=e)

        task = asyncio.create_task(self._content_store.delete_by_filters(
            filters=Filter(
                must=[
                    FieldCondition(
                        key="document_uid",
                        match=MatchAny(any=all_ids),
                    ),
                    FieldCondition(
                        key="type",
                        match=MatchValue(value="chunk"),
                    ),
                ]
            ),
            hard_delete=hard_delete
        ))
        task.add_done_callback(_log_task_result)

    async def delete_nodes(
        self,
        node_ids: list[str],
        hard_delete: bool = True,
        user_uid: str | None = None,
    ) -> None:
        """Bulk-delete multiple nodes (and all their descendants) in one round-trip.

        Semantics match `delete_node` per-row but the work is amortized:

          - **One** `get_nodes(ids)` for the full id set.
          - **One** multi-root BFS via `get_nodes_descendants` instead
            of N separate descendant walks.
          - Snapshot writes scheduled via `_schedule_note_snapshot`
            (fire-and-forget under the semaphore) — `delete_node` used
            to await each snapshot synchronously, which was the
            dominant cost on mass-deletes.
          - **One** `_content_store.delete(all_ids)` and **one**
            `delete_by_filters` for the chunk cleanup.

        Wired by the collab apply path when a single batch contains
        multiple `node.remove` ops (mass selection delete).
        """
        if not node_ids:
            return

        # Deduplicate — a peer racing two clients could conceivably ship
        # the same id twice; filter early so descendants aren't walked
        # twice from the same root.
        unique_root_ids = list(dict.fromkeys(node_ids))
        existing_roots = await self.get_nodes(unique_root_ids)
        if not existing_roots:
            return

        descendants = await self.get_nodes_descendants(
            [n.id for n in existing_roots],
        )
        all_to_delete: list[Note] = list(existing_roots) + list(descendants)
        all_ids = [n.id for n in all_to_delete]

        # Fire-and-forget snapshots — `delete_node` used to await each
        # one synchronously, which dominated wall-time on mass deletes.
        # The semaphore inside `_schedule_note_snapshot` bounds the
        # concurrent OpenAI / pg traffic.
        if self._note_revision_store is not None:
            for n in all_to_delete:
                self._schedule_note_snapshot(n, user_uid=user_uid)

        await self._content_store.delete(all_ids, hard_delete=hard_delete)

        # One filter-delete covers chunks for every removed node id.
        def _log_task_result(task: asyncio.Task) -> None:
            try:
                task.result()
            except Exception as e:
                logger.exception(
                    "Background delete_by_filters (bulk chunk cleanup) failed",
                    exc_info=e,
                )

        task = asyncio.create_task(self._content_store.delete_by_filters(
            filters=Filter(
                must=[
                    FieldCondition(
                        key="document_uid",
                        match=MatchAny(any=all_ids),
                    ),
                    FieldCondition(
                        key="type",
                        match=MatchValue(value="chunk"),
                    ),
                ]
            ),
            hard_delete=hard_delete,
        ))
        task.add_done_callback(_log_task_result)

    async def restore_latest_note_revision(self, node_id: str, user_uid: str | None = None) -> Note | None:
        """Undo the latest saved revision for a note and return the restored note."""
        if self._note_revision_store is None:
            return None

        revision = await self._note_revision_store.pop_latest_note_revision(node_id)
        if revision is None:
            return None

        current_nodes = await self.get_nodes([node_id])

        restored_note = deserialize_note_snapshot(revision.compression, revision.snapshot_compressed)
        payload = restored_note.model_dump(exclude_none=False)
        payload["id"] = restored_note.id

        if current_nodes:
            await self._content_store.update([payload])
        else:
            await self._content_store.add([restored_note])

        return restored_note

    async def get_nodes(self, node_ids: list[str]) -> list[Note]:
        """Retrieve nodes by their IDs."""
        results = await self._content_store.get(node_ids)
        return [result.resource for result in results]

    async def add_links(self, links: list[Link]):
        """Add links to the graph."""
        await self._content_store.add(links)

    async def update_link(self, link_id: str, data: dict):
        """Patch a link in the graph by merging into the stored payload."""
        existing_links = await self.get_links([link_id])
        if not existing_links:
            return

        existing_link = existing_links[0]
        merged_payload = self._deep_merge_dict(
            existing_link.model_dump(exclude_none=False),
            data,
        )
        merged_payload["id"] = link_id

        await self._content_store.update([merged_payload])

    async def update_links(self, updates: list[tuple[str, dict]]) -> None:
        """Bulk-patch multiple links in one DB round-trip.

        Mirrors `patch_notes`: single `get_links` for all ids, in-memory
        merges (input order — same-id duplicates stack), single
        `_content_store.update` write. Used by the collab apply path
        when a batch carries multiple `edge.update` ops.
        """
        if not updates:
            return
        unique_ids = list(dict.fromkeys(link_id for link_id, _ in updates))
        existing_links = await self.get_links(unique_ids)
        by_id: dict[str, Link] = {link.id: link for link in existing_links}

        merged_payloads: list[dict] = []
        for link_id, data in updates:
            cur = by_id.get(link_id)
            if cur is None:
                continue
            merged = self._deep_merge_dict(
                cur.model_dump(exclude_none=False),
                data,
            )
            merged["id"] = link_id
            # Re-validate as Link so subsequent same-id merges in this
            # batch see the result of the previous one.
            try:
                merged_link = Link.model_validate(merged)
            except Exception:
                logger.exception("update_links: failed to validate merged link id=%s", link_id)
                continue
            by_id[link_id] = merged_link
            merged_payloads.append(merged_link.model_dump(exclude_none=False))

        if merged_payloads:
            await self._content_store.update(merged_payloads)

    async def delete_link(self, link_id: str):
        """Delete a link from the graph."""
        await self._content_store.delete([link_id], hard_delete=True)

    async def delete_links(self, link_ids: list[str]) -> None:
        """Bulk-delete multiple links in one round-trip.

        No descendant walk (links don't have children) and no snapshot
        path (links aren't versioned in `NoteRevisionStore`), so this
        collapses to a single `_content_store.delete(ids)` regardless
        of N.
        """
        if not link_ids:
            return
        unique_ids = list(dict.fromkeys(link_ids))
        await self._content_store.delete(unique_ids, hard_delete=True)

    async def get_links(self, link_ids: list[str]) -> list[Link]:
        """Retrieve links by their IDs."""
        results = await self._content_store.get(link_ids)
        return [result.resource for result in results]

    async def get_graph(self, graph_uid: str, root_id: str | None = None) -> Graph | None:
        """Retrieve the entire graph by its UID."""
        async with self._pg_pool.acquire() as conn:
            graph = await get_graph_by_uid(conn, graph_uid)
        if not graph:
            return None

        if root_id is not None:
            root_nodes = await self.get_nodes([root_id])
            if not root_nodes:
                raise ValueError("root node not found")
            root_node = root_nodes[0]
            if root_node.graph_uid != graph_uid:
                raise ValueError("root node does not belong to graph")

        node_must_filters: list[FieldCondition] = [
            FieldCondition(
                key="graph_uid",
                match=MatchValue(value=graph_uid),
            ),
            FieldCondition(
                key="type",
                match=MatchAny(any=["note", "document"]),
            ),
        ]
        node_results = await self._content_store.filt(
            filters=Filter(
                must=node_must_filters
            )
        )
        graph.nodes = [
            node
            for node in (result.resource for result in node_results)
            if (root_id is None and node.parent_id is None)
            or (root_id is not None and node.parent_id == root_id)
        ]

        link_results = await self._content_store.filt(
            filters=Filter(
                must=[
                    FieldCondition(
                        key="graph_uid",
                        match=MatchValue(value=graph_uid),
                    ),
                    FieldCondition(
                        key="type",
                        match=MatchValue(value="link"),
                    ),
                ]
            )
        )
        graph.edges = [
            result.resource
            for result in link_results
            if isinstance(result.resource, Link)
            and self._link_is_visible_in_scope(result.resource, root_id)
        ]

        return graph

    async def get_node_path(self, graph_uid: str, node_id: str) -> list[Note]:
        """Return node path from root to the target node (inclusive)."""
        path: list[Note] = []
        current_id: str | None = node_id
        visited: set[str] = set()

        while current_id:
            if current_id in visited:
                # Safety against corrupted cyclic parent chains.
                break
            visited.add(current_id)

            nodes = await self.get_nodes([current_id])
            if not nodes:
                break
            node = nodes[0]
            if node.graph_uid != graph_uid:
                return []
            path.append(node)
            current_id = node.parent_id

        path.reverse()
        return path

    async def get_nodes_descendants(self, node_ids: list[str]) -> list[Note]:
        """Multi-root BFS — return descendants for every id in `node_ids`.

        Used by the bulk `delete_nodes` path so one round-trip per BFS
        level covers all subtrees instead of N separate single-root
        walks. Roots themselves are NOT included in the return; the
        caller already has them.

        Visit-once semantics ensure a node isn't returned twice when
        two requested roots share a subtree.
        """
        if not node_ids:
            return []
        root_nodes = await self.get_nodes(node_ids)
        if not root_nodes:
            return []
        # Group roots by graph_uid — descendants only exist within the
        # same graph, and a multi-graph delete would be a bug anyway,
        # but we guard against it.
        graphs: dict[str | None, list[str]] = {}
        for n in root_nodes:
            graphs.setdefault(n.graph_uid, []).append(n.id)

        all_descendants: list[Note] = []
        for graph_uid, seed_ids in graphs.items():
            if graph_uid is None:
                continue
            visited: set[str] = set(seed_ids)
            frontier: list[str] = list(seed_ids)
            while frontier:
                results = await self._content_store.filt(
                    filters=Filter(
                        must=[
                            FieldCondition(
                                key="graph_uid",
                                match=MatchValue(value=graph_uid),
                            ),
                            FieldCondition(
                                key="type",
                                match=MatchAny(any=["note", "document"]),
                            ),
                            FieldCondition(
                                key="parent_id",
                                match=MatchAny(any=frontier),
                            ),
                        ]
                    )
                )
                next_frontier: list[str] = []
                for result in results:
                    node = result.resource
                    if not isinstance(node, Note):
                        continue
                    if node.id in visited:
                        continue
                    visited.add(node.id)
                    all_descendants.append(node)
                    next_frontier.append(node.id)
                frontier = next_frontier
        return all_descendants

    async def get_node_descendants(self, node_id: str) -> list[Note]:
        """Return all descendants for a node using BFS on parent_id."""
        # TODO(folder): validate root type is note/document and graph_uid is present.
        # If root is malformed or not a traversable node, return [] (or raise) explicitly.
        root_nodes = await self.get_nodes([node_id])
        if not root_nodes:
            return []

        root = root_nodes[0]
        graph_uid = root.graph_uid

        descendants: list[Note] = []
        visited: set[str] = {node_id}
        frontier: list[str] = [node_id]

        while frontier:
            # TODO(folder): exclude soft-deleted descendants by adding deleted_at == null
            # in this filter once null filtering is standardized in the Qdrant layer.
            # TODO(folder): chunk large frontiers (e.g. 200-500 ids) and query per chunk
            # to avoid oversized MatchAny filters on very wide/deep subtrees.
            results = await self._content_store.filt(
                filters=Filter(
                    must=[
                        FieldCondition(
                            key="graph_uid",
                            match=MatchValue(value=graph_uid),
                        ),
                        FieldCondition(
                            key="type",
                            match=MatchAny(any=["note", "document"]),
                        ),
                        FieldCondition(
                            key="parent_id",
                            match=MatchAny(any=frontier),
                        ),
                    ]
                )
            )

            level_nodes: list[Note] = []
            next_frontier: list[str] = []
            for result in results:
                node = result.resource
                if not isinstance(node, Note):
                    continue
                if node.id in visited:
                    continue
                visited.add(node.id)
                level_nodes.append(node)
                next_frontier.append(node.id)

            if not level_nodes:
                break

            descendants.extend(level_nodes)
            frontier = next_frontier

        return descendants

    async def add_graph(self, graph: Graph, user_uid: str) -> Graph:
        """Create a new graph and assign its owner in one atomic transaction.

        Graph + owner `graph_user` row commit together so no window exists
        where a private board is readable without an owner — which
        previously let a concurrent GET /boards/{id} pass the graph-exists
        check then fail the role check (404) until the owner row landed.
        """
        async with self._pg_pool.acquire() as conn:
            async with conn.transaction():
                await create_graph(conn, graph)
                await add_user_to_graph_by_uid(conn, graph.uid, user_uid, "owner")

    async def update_graph(self, graph_uid: str, data: dict):
        """Update an existing graph."""
        async with self._pg_pool.acquire() as conn:
            await update_graph_by_uid(conn, graph_uid, data)

    async def delete_graph(self, graph_uid: str, hard_delete: bool = False):
        """Delete a graph by its UID.

        Qdrant content is removed BEFORE the Postgres row so a content-store
        failure leaves the graph listed (PG row intact) and the delete
        retriable. The old order committed the PG row first and orphaned
        Qdrant points on a content-store failure — no board row remained to
        drive a retry.
        """
        await self._content_store.delete_by_filters(
            filters={"must": [{"key": "graph_uid", "match": {"value": graph_uid}}]},
            hard_delete=hard_delete
        )
        async with self._pg_pool.acquire() as conn:
            if not hard_delete:
                await delete_graph_by_uid(conn, graph_uid)
            else:
                await _dangerous_hard_delete_graph_by_uid(conn, graph_uid)

    async def list_graphs(self, user_uid: str) -> list[tuple[Graph, str, str | None]]:
        """List the user's boards with their per-board role + owner email.

        Returns one tuple per accessible board: `(graph, role, owner_email)`.
        The role drives the sidebar's "My boards" / "Shared with me"
        split; owner_email surfaces as a hint on shared rows.
        """
        async with self._pg_pool.acquire() as conn:
            return await list_graphs_by_user_uid(conn, user_uid)

    async def get_owner_uid(self, graph_uid: str) -> str | None:
        """Return the user_uid of the board's owner, or None when missing."""
        async with self._pg_pool.acquire() as conn:
            return await get_owner_uid_by_graph_uid(conn, graph_uid)

    async def list_members(self, graph_uid: str) -> list[dict]:
        """Owner-facing list of `(user_uid, email, role, joined_at)`."""
        async with self._pg_pool.acquire() as conn:
            return await list_members_for_graph(conn, graph_uid)

    async def remove_member(self, graph_uid: str, user_uid: str) -> int:
        """Drop one graph_user row. Returns the row count removed (0 or 1)."""
        async with self._pg_pool.acquire() as conn:
            return await remove_user_from_graph_by_uid(
                conn, user_uid=user_uid, graph_uid=graph_uid,
            )

    async def get_graph_role(self, graph_uid: str, user_uid: str) -> str | None:
        """Return user's graph role, or None if user has no access."""
        async with self._pg_pool.acquire() as conn:
            return await get_graph_role_by_user_uid(conn, graph_uid, user_uid)

    async def get_graph_metadata(self, graph_uid: str) -> Graph | None:
        """Return graph metadata without loading nodes/edges from Qdrant."""
        async with self._pg_pool.acquire() as conn:
            return await get_graph_by_uid(conn, graph_uid)

    async def close(self):
        """Close the store. Drains pending snapshot tasks; closes the pool only if owned."""
        if self._snapshot_tasks:
            pending = list(self._snapshot_tasks)
            await asyncio.gather(*pending, return_exceptions=True)
        if self._pg_pool and self._owns_pool:
            await self._pg_pool.close()
        await self._content_store.close()
