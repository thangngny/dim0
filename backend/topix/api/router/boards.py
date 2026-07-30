"""Graph API Router."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.params import Body, Path

from topix.agents.assistant.code import DEFAULT_LANGUAGE, RUNNABLE_LANGUAGES, execute_code
from topix.agents.datatypes.context import Context
from topix.agents.describe_board import DescribeBoard
from topix.agents.run import AgentRunner
from topix.agents.sessions import AssistantSession
from topix.api.datatypes.requests import (
    AddLinksRequest,
    AddNotesRequest,
    BoardVisibilityUpdateRequest,
    GraphUpdateRequest,
    LinkUpdateRequest,
    NoteUpdateRequest,
)
from topix.api.utils.decorators import with_standard_response
from topix.api.utils.security import (
    get_current_user_uid,
    verify_board_member,
    verify_board_read_access,
)
from topix.api.utils.thumbnail import load_png_as_data_url, save_thumbnail
from topix.datatypes.graph.graph import Graph
from topix.datatypes.note.style import NodeType
from topix.store.chat import ChatStore
from topix.store.graph import GraphStore

router = APIRouter(
    prefix="/boards",
    tags=["boards"],
    responses={404: {"description": "Not found"}},
)


@router.put("/", include_in_schema=False)
@router.put("")
@with_standard_response
async def create_graph(
    response: Response,
    request: Request,
    user_id: Annotated[str, Depends(get_current_user_uid)]
):
    """Create a new graph for the user."""
    store: GraphStore = request.app.graph_store

    new_graph = Graph(user_uid=user_id)
    await store.add_graph(graph=new_graph, user_uid=user_id)
    return {"graph_id": new_graph.uid}


@router.patch("/{graph_id}/", include_in_schema=False)
@router.patch("/{graph_id}")
@with_standard_response
async def update_graph(
    response: Response,
    request: Request,
    graph_id: Annotated[str, Path(description="Graph ID")],
    user_id: Annotated[str, Depends(get_current_user_uid)],
    _: Annotated[None, Depends(verify_board_member)],
    body: Annotated[GraphUpdateRequest, Body(description="Graph update data")]
):
    """Update an existing graph by its ID."""
    store: GraphStore = request.app.graph_store
    return await store.update_graph(graph_uid=graph_id, data=body.data)


@router.get("/{graph_id}/research-progress/", include_in_schema=False)
@router.get("/{graph_id}/research-progress")
@with_standard_response
async def board_research_progress(
    response: Response,
    request: Request,
    graph_id: Annotated[str, Path(description="Graph ID")],
    user_id: Annotated[str, Depends(get_current_user_uid)],
    _: Annotated[None, Depends(verify_board_read_access)],
    session_id: Annotated[str | None, Query(description="Optional research session id")] = None,
):
    """Poll live deep-research sub-agent cards for this board (canvas panel)."""
    from topix.integrations.research_progress import get_board_progress, snapshot_dict

    prog = get_board_progress(graph_id, session_id=session_id)
    if prog is None:
        return {
            "board_id": graph_id,
            "session_id": session_id,
            "active": False,
            "agents": [],
            "events": [],
        }
    snap = snapshot_dict(prog)
    snap["active"] = not (prog.completed or prog.failed)
    return snap


@router.get("/{graph_id}/contents/", include_in_schema=False)
@router.get("/{graph_id}/contents")
@with_standard_response
async def list_board_contents(
    response: Response,
    request: Request,
    graph_id: Annotated[str, Path(description="Graph ID")],
    user_id: Annotated[str, Depends(get_current_user_uid)],
    _: Annotated[None, Depends(verify_board_read_access)],
    parent_id: Annotated[str | None, Query(description="Folder ID to list children of; omit for top level")] = None,
):
    """List a board's surface-kind nodes (sheet/folder/code-sandbox/widget) at one level."""
    store: GraphStore = request.app.graph_store
    graph = await store.get_graph(graph_uid=graph_id, root_id=parent_id)
    if not graph:
        return {"items": []}

    surface_kinds = {"sheet", "folder", "code-sandbox", "widget"}
    items = []
    for node in graph.nodes:
        kind = node.style.type if node.style else None
        if kind not in surface_kinds:
            continue
        # Serialize the inner icon value only (drop the IconProperty wrapper)
        # so the response matches the frontend's IconProperty["icon"] shape
        # and stays small for big sidebars.
        icon_property = node.properties.icon_data
        icon_payload = (
            icon_property.icon.model_dump()
            if icon_property and icon_property.icon
            else None
        )
        items.append({
            "id": node.id,
            "label": node.label.markdown if node.label else None,
            "kind": kind,
            "parent_id": node.parent_id,
            "icon_data": icon_payload,
        })
    return {"items": items}


@router.post("/{graph_id}:describe/", include_in_schema=False)
@router.post("/{graph_id}:describe")
@with_standard_response
async def describe_board(
    response: Response,
    request: Request,
    graph_id: Annotated[str, Path(description="Graph ID")],
    user_id: Annotated[str, Depends(get_current_user_uid)],
    _: Annotated[None, Depends(verify_board_member)],
    chat_id: Annotated[str, Query(description="Chat ID to summarize for the board label")],
):
    """Auto-label a board from a chat. No-op if the board already has a non-default label."""
    graph_store: GraphStore = request.app.graph_store
    chat_store: ChatStore = request.app.chat_store

    metadata = await graph_store.get_graph_metadata(graph_uid=graph_id)
    current_label = (metadata.label if metadata else "") or ""
    if current_label.strip() and current_label.strip().lower() != "untitled":
        return {"label": current_label}

    chat = await chat_store.get_chat(chat_id)
    if not chat or chat.user_uid != user_id or chat.graph_uid != graph_id:
        raise HTTPException(status_code=403, detail="Chat does not belong to this board.")

    context = Context()
    session = AssistantSession(session_id=chat_id, chat_store=chat_store)
    board_describer = DescribeBoard()
    label = await AgentRunner.run(board_describer, await session.get_items(), context=context)

    if label:
        await graph_store.update_graph(graph_uid=graph_id, data={"label": label})
    return {"label": label}


@router.delete("/{graph_id}/", include_in_schema=False)
@router.delete("/{graph_id}")
@with_standard_response
async def delete_graph(
    response: Response,
    request: Request,
    graph_id: Annotated[str, Path(description="Graph ID")],
    user_id: Annotated[str, Depends(get_current_user_uid)],
    _: Annotated[None, Depends(verify_board_member)],
):
    """Delete a graph by its ID."""
    store: GraphStore = request.app.graph_store

    await store.delete_graph(graph_uid=graph_id, hard_delete=True)
    return {"message": "Board deleted successfully"}


@router.get("/{graph_id}/", include_in_schema=False)
@router.get("/{graph_id}")
@with_standard_response
async def get_graph(
    response: Response,
    request: Request,
    graph_id: Annotated[str, Path(description="Graph ID")],
    user_id: Annotated[str, Depends(get_current_user_uid)],
    _: Annotated[None, Depends(verify_board_read_access)],
    root_id: Annotated[str | None, Query(description="Root node ID for subgraph (direct children only)")] = None,
):
    """Get a graph by its ID."""
    store: GraphStore = request.app.graph_store

    try:
        graph = await store.get_graph(graph_uid=graph_id, root_id=root_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    if not graph:
        raise HTTPException(status_code=404, detail="Graph not found")

    if graph.thumbnail and graph.thumbnail.startswith("file://"):
        graph.thumbnail = load_png_as_data_url(graph.thumbnail)

    role = await store.get_graph_role(graph_uid=graph_id, user_uid=user_id)
    can_edit = role in {"owner", "member"}

    return {
        "graph": graph.model_dump(exclude_none=True),
        "can_edit": can_edit,
        # `role` is one of "owner" | "member" | "viewer" | None
        # (None for public-visibility boards a non-member is viewing).
        # The frontend uses this to gate the Share button (owner-only).
        "role": role,
    }


@router.get("/", include_in_schema=False)
@router.get("")
@with_standard_response
async def list_graphs(
    response: Response,
    request: Request,
    user_id: Annotated[str, Depends(get_current_user_uid)]
):
    """List all graphs for the user, each annotated with role + owner email."""
    store: GraphStore = request.app.graph_store

    rows = await store.list_graphs(user_uid=user_id)

    items = []
    for graph, role, owner_email in rows:
        # Convert file:// URLs to data URLs (kept here so the model
        # dump below sees the resolved value).
        if graph.thumbnail and graph.thumbnail.startswith("file://"):
            graph.thumbnail = load_png_as_data_url(graph.thumbnail)
        item = graph.model_dump(exclude_none=True)
        item["role"] = role
        # Only attach owner_email for non-owners — saves payload size
        # and matches what the sidebar actually shows ("shared by …").
        if role != "owner":
            item["owner_email"] = owner_email
        items.append(item)

    return {"graphs": items}


@router.post("/{graph_id}/notes/", include_in_schema=False)
@router.post("/{graph_id}/notes")
@with_standard_response
async def add_notes_to_graph(
    response: Response,
    request: Request,
    graph_id: Annotated[str, Path(description="Graph ID")],
    user_id: Annotated[str, Depends(get_current_user_uid)],
    _: Annotated[None, Depends(verify_board_member)],
    body: Annotated[AddNotesRequest, Body(description="Notes to add")]
):
    """Add notes to a graph."""
    store: GraphStore = request.app.graph_store

    notes = body.notes

    for note in notes:
        note.graph_uid = graph_id

    if not notes:
        return {"message": "Received empty note array."}

    await store.add_notes(nodes=notes)
    return {"message": "Notes added to board successfully"}


@router.get("/{graph_id}/notes/{note_id}", include_in_schema=False)
@router.get("/{graph_id}/notes/{note_id}")
@with_standard_response
async def get_note(
    response: Response,
    request: Request,
    graph_id: Annotated[str, Path(description="Graph ID")],
    note_id: Annotated[str, Path(description="Note ID")],
    user_id: Annotated[str, Depends(get_current_user_uid)],
    _: Annotated[None, Depends(verify_board_read_access)],
):
    """Get a note from a graph."""
    store: GraphStore = request.app.graph_store

    notes = await store.get_nodes(node_ids=[note_id])
    if not notes:
        raise HTTPException(status_code=404, detail="Note not found")

    return {"note": notes[0].model_dump(exclude_none=True)}


@router.post("/{graph_id}/notes/{note_id}:execute", include_in_schema=False)
@router.post("/{graph_id}/notes/{note_id}:execute")
@with_standard_response
async def execute_note_code(
    response: Response,
    request: Request,
    graph_id: Annotated[str, Path(description="Graph ID")],
    note_id: Annotated[str, Path(description="Note ID")],
    user_id: Annotated[str, Depends(get_current_user_uid)],
    _: Annotated[None, Depends(verify_board_member)],
):
    """Execute code stored in a code sandbox note, in its declared language."""
    store: GraphStore = request.app.graph_store

    notes = await store.get_nodes(node_ids=[note_id])
    if not notes:
        raise HTTPException(status_code=404, detail="Note not found")

    note = notes[0]
    if note.graph_uid != graph_id:
        raise HTTPException(status_code=404, detail="Note not found")
    if note.style.type != NodeType.CODE_SANDBOX:
        raise HTTPException(status_code=400, detail="Note is not a code sandbox")

    language = note.properties.programming_language.text or DEFAULT_LANGUAGE
    if language not in RUNNABLE_LANGUAGES:
        raise HTTPException(
            status_code=400,
            detail=f"Language '{language}' is not runnable",
        )

    code = note.content.markdown if note.content else ""
    result = await execute_code(code, language)
    return result.model_dump(exclude_none=True)


@router.get("/{graph_id}/notes/{note_id}/path", include_in_schema=False)
@router.get("/{graph_id}/notes/{note_id}/path")
@with_standard_response
async def get_note_path(
    response: Response,
    request: Request,
    graph_id: Annotated[str, Path(description="Graph ID")],
    note_id: Annotated[str, Path(description="Note ID")],
    user_id: Annotated[str, Depends(get_current_user_uid)],
    _: Annotated[None, Depends(verify_board_read_access)],
):
    """Get full path from root to a note."""
    store: GraphStore = request.app.graph_store

    path = await store.get_node_path(graph_uid=graph_id, node_id=note_id)
    if not path:
        raise HTTPException(status_code=404, detail="Note path not found")

    return {"path": [node.model_dump(exclude_none=True) for node in path]}


@router.patch("/{graph_id}/notes/{note_id}/", include_in_schema=False)
@router.patch("/{graph_id}/notes/{note_id}")
@with_standard_response
async def update_note(
    response: Response,
    request: Request,
    graph_id: Annotated[str, Path(description="Graph ID")],
    note_id: Annotated[str, Path(description="Note ID")],
    user_id: Annotated[str, Depends(get_current_user_uid)],
    _: Annotated[None, Depends(verify_board_member)],
    body: Annotated[NoteUpdateRequest, Body(description="Note update data")]
):
    """Update a note or document node in a graph."""
    store: GraphStore = request.app.graph_store

    updated_note = await store.patch_note(node_id=note_id, data=body.data, user_uid=user_id)
    if updated_note is None:
        raise HTTPException(status_code=404, detail="Note not found")
    return {"message": "Note updated successfully"}


@router.delete("/{graph_id}/notes/{note_id}/", include_in_schema=False)
@router.delete("/{graph_id}/notes/{note_id}")
@with_standard_response
async def remove_note_from_graph(
    response: Response,
    request: Request,
    graph_id: Annotated[str, Path(description="Graph ID")],
    note_id: Annotated[str, Path(description="Note ID")],
    user_id: Annotated[str, Depends(get_current_user_uid)],
    _: Annotated[None, Depends(verify_board_member)],
):
    """Remove notes from a graph."""
    store: GraphStore = request.app.graph_store

    await store.delete_node(node_id=note_id, user_uid=user_id)
    return {"message": "Note removed from board successfully"}


@router.post("/{graph_id}/notes/{note_id}:restore-latest", include_in_schema=False)
@router.post("/{graph_id}/notes/{note_id}:restore-latest")
@with_standard_response
async def restore_latest_note_revision(
    response: Response,
    request: Request,
    graph_id: Annotated[str, Path(description="Graph ID")],
    note_id: Annotated[str, Path(description="Note ID")],
    user_id: Annotated[str, Depends(get_current_user_uid)],
    _: Annotated[None, Depends(verify_board_member)],
):
    """Restore the latest saved note revision for a board note."""
    store: GraphStore = request.app.graph_store

    restored_note = await store.restore_latest_note_revision(node_id=note_id, user_uid=user_id)
    if restored_note is None or restored_note.graph_uid != graph_id:
        raise HTTPException(status_code=404, detail="Note revision not found")

    return {"note": restored_note.model_dump(exclude_none=True)}


@router.post("/{graph_id}/links/", include_in_schema=False)
@router.post("/{graph_id}/links")
@with_standard_response
async def add_links_to_graph(
    response: Response,
    request: Request,
    graph_id: Annotated[str, Path(description="Graph ID")],
    user_id: Annotated[str, Depends(get_current_user_uid)],
    _: Annotated[None, Depends(verify_board_member)],
    body: Annotated[AddLinksRequest, Body(description="Links to add")]
):
    """Add links to a graph."""
    store: GraphStore = request.app.graph_store

    links = body.links
    for link in links:
        link.graph_uid = graph_id

    if not links:
        return {"message": "Received empty link array."}

    await store.add_links(links=links)
    return {"message": "Links added to board successfully."}


@router.get("/{graph_id}/links/{link_id}", include_in_schema=False)
@router.get("/{graph_id}/links/{link_id}")
@with_standard_response
async def get_link(
    response: Response,
    request: Request,
    graph_id: Annotated[str, Path(description="Graph ID")],
    link_id: Annotated[str, Path(description="Link ID")],
    user_id: Annotated[str, Depends(get_current_user_uid)],
    _: Annotated[None, Depends(verify_board_read_access)],
):
    """Get a link from a graph."""
    store: GraphStore = request.app.graph_store

    links = await store.get_links(link_ids=[link_id])
    if not links:
        raise HTTPException(status_code=404, detail="Link not found")

    return {"link": links[0].model_dump(exclude_none=True)}


@router.patch("/{graph_id}/links/{link_id}/", include_in_schema=False)
@router.patch("/{graph_id}/links/{link_id}")
@with_standard_response
async def update_link(
    response: Response,
    request: Request,
    graph_id: Annotated[str, Path(description="Graph ID")],
    link_id: Annotated[str, Path(description="Link ID")],
    user_id: Annotated[str, Depends(get_current_user_uid)],
    _: Annotated[None, Depends(verify_board_member)],
    body: Annotated[LinkUpdateRequest, Body(description="Link update data")]
):
    """Update a link in a graph."""
    store: GraphStore = request.app.graph_store

    await store.update_link(link_id=link_id, data=body.data)
    return {"message": "Link updated successfully"}


@router.delete("/{graph_id}/links/{link_id}/", include_in_schema=False)
@router.delete("/{graph_id}/links/{link_id}")
@with_standard_response
async def remove_link_from_graph(
    response: Response,
    request: Request,
    graph_id: Annotated[str, Path(description="Graph ID")],
    link_id: Annotated[str, Path(description="Link ID")],
    user_id: Annotated[str, Depends(get_current_user_uid)],
    _: Annotated[None, Depends(verify_board_member)],
):
    """Remove links from a graph."""
    store: GraphStore = request.app.graph_store

    await store.delete_link(link_id=link_id)
    return {"message": "Link removed from board successfully"}


@router.post("/{graph_id}/thumbnail/", include_in_schema=False)
@router.post("/{graph_id}/thumbnail")
@with_standard_response
async def save_graph_thumbnail(
    request: Request,
    graph_id: Annotated[str, Path(description="Graph ID")],
    user_id: Annotated[str, Depends(get_current_user_uid)],
    _: Annotated[None, Depends(verify_board_member)],
    file: UploadFile = File(...),
):
    """Save a thumbnail image for the graph."""
    # Cap the upload so a large/hostile payload can't exhaust memory or
    # fill the disk — stream in chunks and reject over the budget.
    max_thumbnail_bytes = 5 * 1024 * 1024
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_thumbnail_bytes:
            raise HTTPException(status_code=413, detail="Thumbnail too large (max 5MB)")
        chunks.append(chunk)
    file_bytes = b"".join(chunks)
    path = save_thumbnail(graph_id, file_bytes)
    store: GraphStore = request.app.graph_store

    await store.update_graph(graph_id, {"thumbnail": path})
    return {"path": path}


@router.patch("/{graph_id}/visibility/", include_in_schema=False)
@router.patch("/{graph_id}/visibility")
@with_standard_response
async def update_graph_visibility(
    response: Response,
    request: Request,
    graph_id: Annotated[str, Path(description="Graph ID")],
    user_id: Annotated[str, Depends(get_current_user_uid)],
    _: Annotated[None, Depends(verify_board_member)],
    body: Annotated[BoardVisibilityUpdateRequest, Body(description="Board visibility update data")],
):
    """Update board visibility."""
    store: GraphStore = request.app.graph_store
    await store.update_graph(graph_uid=graph_id, data={"visibility": body.visibility})
    return {"message": "Board visibility updated successfully"}
