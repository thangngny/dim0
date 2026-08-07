"""Assistant session management."""

from agents.memory import Session

from topix.datatypes.chat.chat import Message
from topix.store.chat import ChatStore

MAX_RETRIEVAL_MESSAGES = 16


class AssistantSession(Session):
    """Session for the assistant agent."""

    def __init__(self, session_id: str, chat_store: ChatStore):
        """Init method."""
        self._session_id = session_id
        self._chat_store = chat_store

    async def get_items(self, limit: int = MAX_RETRIEVAL_MESSAGES) -> list[dict]:
        """Get items from the session."""
        messages = await self._chat_store.get_messages(
            chat_uid=self._session_id, limit=limit
        )
        return [msg.to_chat_message() for msg in messages]

    async def add_items(self, items: list[dict | Message]) -> None:
        """Add items to the session."""
        await self._chat_store.add_messages(chat_uid=self._session_id, messages=items)

    async def pop_item(self) -> dict | None:
        """Pop the last item from the session."""
        res = await self._chat_store.pop_message(chat_uid=self._session_id)
        if res:
            return res.to_chat_message()
        return None

    async def clear_session(self) -> None:
        """Clear the session."""
        await self._chat_store.delete_chat(chat_uid=self._session_id, hard_delete=True)


def _extract_text(content: object) -> str:
    """Flatten a chat message's content (string or list of blocks) into text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return " ".join(parts)
    return ""


def fallback_label_from_items(items: list[dict] | None, max_len: int = 60) -> str | None:
    """Derive a short fallback label from the first user message in items.

    The describe agents request a JSON title but some providers (e.g. Ollama
    cloud models) occasionally return prose instead, which would raise a parse
    error. This gives the caller a meaningful best-effort label so the
    auto-labeling endpoint never 500s.
    """
    for item in items or []:
        if isinstance(item, dict) and item.get("role") == "user":
            text = _extract_text(item.get("content")).strip()
            if text:
                first_line = text.splitlines()[0].strip()
                return first_line[:max_len] or None
            break
    return None
