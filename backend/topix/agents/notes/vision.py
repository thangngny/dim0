"""Vision tool — let the agent read an image node on the canvas.

Uses a vision-capable model on Ollama Cloud (default `gemma4:31b`, which
accepts image input) so the agent can answer questions about image notes
(screenshots, moodboards, diagrams pasted as images). Same Ollama Cloud
path + key as the chat agent, so no new credentials are needed.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import os

from agents import FunctionTool, RunContextWrapper

from topix.agents.datatypes.context import Context
from topix.agents.datatypes.tools import AgentToolName
from topix.agents.tool_handler import ToolHandler
from topix.store.graph import GraphStore
from topix.utils.file import get_file_path

logger = logging.getLogger(__name__)


def _ollama_chat_base_url() -> str:
    from topix.integrations.research_clarify import _ollama_chat_base_url as _base
    return _base()


def _vision_model() -> str:
    return os.getenv("DIM0_VISION_MODEL") or os.getenv("OLLAMA_VISION_MODEL") or "gemma4:31b"


def _resolve_data_url(url: str) -> str:
    """Turn an image property URL (data: or file://) into a data: URL for the vision API."""
    if url.startswith("data:"):
        return url
    # file:// path or a raw path — read from disk + base64-encode.
    path = get_file_path(url)
    mime, _ = mimetypes.guess_type(path)
    if not mime:
        mime = "image/png"
    with open(path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")
    return f"data:{mime};base64,{encoded}"


async def _describe_image(data_url: str, question: str) -> str:
    """Call the vision model with an image + a short question, return the text reply."""
    import litellm

    litellm.drop_params = True
    model = _vision_model()
    api_key = os.getenv("OLLAMA_API_KEY") or "ollama"
    base_url = _ollama_chat_base_url()
    prompt = question.strip() or "Describe this image concisely: what it shows, mood, style, and any text visible."
    resp = await litellm.acompletion(
        model=f"openai/{model}",
        api_base=base_url,
        api_key=api_key,
        max_tokens=400,
        temperature=0.1,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
    )
    return (resp.choices[0].message.content or "").strip()


def create_describe_image_tool(
    graph_store: GraphStore,
    graph_uid: str,
) -> FunctionTool:
    """Build a describe-image tool bound to the current board scope."""

    async def describe_image(
        _wrapper: RunContextWrapper[Context],
        note_id: str,
        question: str = "",
    ) -> str:
        """Read an image node on the board with the vision model.

        Use this when a note is an image (screenshot, moodboard, pasted
        picture) and you need to understand its visual content — the
        text `get_note` returns is empty for image notes. Pass an
        optional `question` to focus the description (e.g. "what is the
        color palette?", "what text is visible?").

        Args:
            note_id (str): Exact id of the image note to read.
            question (str): Optional focus question for the description.

        """
        existing = await graph_store.get_nodes([note_id])
        if not existing:
            raise ValueError(f"Note {note_id} was not found.")
        note = existing[0]
        if note.graph_uid != graph_uid:
            raise ValueError("Note does not belong to the current board scope.")
        image_prop = note.properties.image_url if note.properties else None
        url = image_prop.image.url if image_prop and image_prop.image else None
        if not url:
            raise ValueError(
                f"Note {note_id} is not an image note (no image_url)."
            )
        try:
            data_url = _resolve_data_url(url)
            description = await _describe_image(data_url, question)
        except Exception as exc:  # noqa: BLE001
            logger.warning("describe_image failed for %s: %s", note_id, exc)
            return f"(could not read image: {exc})"
        return description

    return ToolHandler.convert_func_to_tool(
        describe_image,
        tool_name=AgentToolName.DESCRIBE_IMAGE,
        tool_description=None,
    )