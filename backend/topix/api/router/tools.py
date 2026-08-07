"""Tools API Router."""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Request, Response

from topix.agents.datatypes.context import Context
from topix.agents.drawify.drawify import DrawifyAgent, convert_drawify_output_to_notes_links
from topix.agents.mindmap.mapify import MapifyAgent, convert_mapify_output_to_notes_links
from topix.agents.mindmap.notify import NotifyAgent, convert_notify_output_to_notes_links
from topix.agents.mindmap.quizify.quizify import QuizifyAgent
from topix.agents.mindmap.schemify.schemify import SchemifyAgent, convert_schemify_output_to_notes_links
from topix.agents.mindmap.summify.summify import SummifyAgent
from topix.agents.run import AgentRunner
from topix.agents.translate.translate import TranslateAgent
from topix.api.datatypes.requests import ConvertToMindMapRequest, TranslateTextRequest, WebPagePreviewRequest
from topix.api.utils.decorators import with_standard_response
from topix.api.utils.rate_limit.dependency import rate_limiter
from topix.api.utils.security import get_current_user_uid
from topix.api.utils.streaming_tool import stream_tool_conversion
from topix.datatypes.note.note import Note
from topix.datatypes.resource import RichText
from topix.utils.web.preview import preview_webpage

router = APIRouter(
    prefix="/tools",
    tags=["tools"],
    responses={404: {"description": "Not found"}},
)


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


@router.post("/webpages/preview/", include_in_schema=False)
@router.post("/webpages/preview")
@with_standard_response
async def link_preview(
    response: Response,
    request: Request,
    user_id: Annotated[str, Depends(get_current_user_uid)],
    body: Annotated[WebPagePreviewRequest, Body(description="Webpage URL to preview")]
):
    """Fetch a preview of the webpage at the given URL."""
    res = preview_webpage(body.url)
    return res.model_dump(exclude_none=True)


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
