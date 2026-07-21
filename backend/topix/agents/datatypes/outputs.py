"""Agent Output Data Types."""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel

from topix.agents.datatypes.annotations import (
    RefAnnotation,
    SearchResult,
)
from topix.agents.datatypes.drawn_graph import DrawnGraph
from topix.agents.mindmap.schemify.datatypes import SchemaOutput
from topix.datatypes.note.style import NodeType


class DisplayWeatherWidgetOutput(BaseModel):
    """Display Weather Widget Output."""

    type: Literal["display_weather_widget"] = "display_weather_widget"
    city: Annotated[
        str,
        "Free-form place description for geocoding. "
        "Include country or state to disambiguate when possible. "
        "Examples: 'Paris, France', 'Austin, TX, USA', 'Bangalore, IN', "
        "'Shibuya, Tokyo, Japan'. Can also be a neighborhood or landmark "
        "like 'Manhattan, New York, USA'."
    ]

    def to_compact_repr(self) -> str:
        """Return a short history-safe summary for the widget request."""
        return f"weather for {self.city}".strip()


class DisplayStockWidgetOutput(BaseModel):
    """Display Stock Widget Output."""

    type: Literal["display_stock_widget"] = "display_stock_widget"
    symbol: Annotated[str, "The stock ticker symbol, e.g. AAPL for Apple Inc."]

    def to_compact_repr(self) -> str:
        """Return a short history-safe summary for the widget request."""
        return f"stock for {self.symbol}".strip()


class DisplayImageSearchWidgetOutput(BaseModel):
    """Display Image Search Widget Output."""

    type: Literal["display_image_search_widget"] = "display_image_search_widget"
    query: Annotated[
        str,
        "The search query for finding relevant images to display in the widget."
    ]
    images: Annotated[
        list[str],
        "List of image URLs returned from the image search. Should be left empty. This will be populated by the frontend."
    ] = []

    def to_compact_repr(self) -> str:
        """Return a short history-safe summary for the widget request."""
        return f'image search "{self.query}"'.strip()


class NewsfeedArticle(BaseModel):
    """Newsfeed article data model."""

    title: str
    url: str
    summary: str
    published_at: str
    source_domain: str
    score: int | None = None
    tags: list[str] = []


class NewsfeedSection(BaseModel):
    """Newsfeed section data model."""

    title: str
    articles: list[NewsfeedArticle]


class NewsfeedOutput(BaseModel):
    """Newsfeed output data model."""

    sections: list[NewsfeedSection]

    def to_compact_repr(self) -> str:
        """Return the number of sections and articles produced."""
        article_count = sum(len(section.articles) for section in self.sections)
        return f"{len(self.sections)} sections, {article_count} articles"


class TopicTracker(BaseModel):
    """Topic data model."""

    description: str
    sub_topics: list[str]
    keywords: list[str]
    seed_sources: list[str]

    def to_compact_repr(self) -> str:
        """Return the tracked topic metadata in compact form."""
        return (
            f"{len(self.sub_topics)} subtopics, "
            f"{len(self.keywords)} keywords, "
            f"{len(self.seed_sources)} sources"
        )


class MapifyTheme(BaseModel):
    """Theme."""

    label: str
    description: str
    subthemes: list[MapifyTheme] = []

    def to_compact_repr(self) -> str:
        """Return the root label and subtheme count."""
        return f'"{self.label}" with {len(self.subthemes)} subthemes'


class NotifyOutput(BaseModel):
    """Notify Output."""

    title: str
    content: str

    def to_compact_repr(self) -> str:
        """Return the generated notification title."""
        return f'title "{self.title}"'.strip()


class TranslateOutput(BaseModel):
    """Translate Output."""

    text: str

    def to_compact_repr(self) -> str:
        """Return a short summary of the translated payload."""
        return f"translated {len(self.text)} chars"


class ImageDescriptionOutput(BaseModel):
    """Output of the image description agent."""

    image_title: str
    image_type: str
    image_summary: str

    def to_compact_repr(self) -> str:
        """Return the described image type and title."""
        return f'{self.image_type}: "{self.image_title}"'


class TopicIllustratorOutput(BaseModel):
    """Output of the topic illustrator agent."""

    image_url: str
    image_title: str
    image_description: str

    def to_compact_repr(self) -> str:
        """Return the generated illustration title."""
        return f'illustration "{self.image_title}"'


class WebSearchOutput(BaseModel):
    """Output from web search tool."""

    type: Literal["web_search"] = "web_search"
    answer: str = ""
    search_results: list[SearchResult]

    def __str__(self) -> str:
        """Convert output to string."""
        if not self.answer:
            # raw search results
            formatted = "Search Results:\n\n"
            for result in self.search_results:
                formatted += (
                    "\n<Source"
                    f"\n  url=\"{result.url}\""
                    f"\n  title=\"{result.title}\""
                    "\n>"
                    f"\n{result.content}\n"
                    "\n</Source>\n"
                )
            return formatted
        else:
            """The final output of the Websearch Agent."""
            return self.answer

    def to_compact_repr(self) -> str:
        """Return a short summary of the answer or source count."""
        if self.answer:
            return f"answered with {len(self.search_results)} sources"
        return f"{len(self.search_results)} search results"


class CodeInterpreterOutput(BaseModel):
    """Output from code interpreter tool."""

    type: Literal["code_interpreter"] = "code_interpreter"
    status: Literal["success", "error", "timeout"]
    stdout: str = ""
    stderr: str = ""
    duration_ms: int

    def __str__(self) -> str:
        """To string method."""
        result = f"Execution status: {self.status}\nDuration: {self.duration_ms}ms"

        if self.stdout:
            result += f"\n\nstdout:\n{self.stdout}"

        if self.stderr:
            result += f"\n\nstderr:\n{self.stderr}"

        return result

    def to_compact_repr(self) -> str:
        """Return execution status with runtime."""
        return f"{self.status} in {self.duration_ms}ms"


class CreateNoteOutput(BaseModel):
    """Output from create note tool."""

    type: Literal["create_note"] = "create_note"
    note_id: Annotated[str, "The unique id of the created note."]
    graph_uid: Annotated[str, "The board id where the note was created."]
    label: Annotated[str | None, "Optional short title stored separately from the note body."] = None
    note_type: Annotated[NodeType, "The final node type used for the created note."]
    parent_id: Annotated[
        str | None,
        "The folder/root note id used as the created note parent, if any."
    ] = None

    def to_compact_repr(self) -> str:
        """Return the created note metadata in a compact, history-safe form."""
        label = f' "{self.label}"' if self.label else ""
        return f'created {self.note_type} note_id="{self.note_id}"{label}'


class WriteNoteOutput(BaseModel):
    """Output from write note tool."""

    type: Literal["write_note"] = "write_note"
    action: Literal["created", "rewritten"]
    note_id: Annotated[str, "The unique id of the created or rewritten note."]
    graph_uid: Annotated[str, "The board id where the note belongs."]
    label: Annotated[str | None, "Optional short title stored separately from the note body."] = None
    note_type: Annotated[NodeType, "The final node type used for the note."]
    parent_id: Annotated[
        str | None,
        "The folder/root note id used as the note parent, if any."
    ] = None

    def to_compact_repr(self) -> str:
        """Return the write action metadata in a compact, history-safe form."""
        label = f' "{self.label}"' if self.label else ""
        return f'{self.action} {self.note_type} note_id="{self.note_id}"{label}'


class EditNoteOutput(BaseModel):
    """Output from edit note tool."""

    type: Literal["edit_note"] = "edit_note"
    note_id: Annotated[str, "The unique id of the edited note."]
    graph_uid: Annotated[str, "The board id where the note belongs."]
    label: Annotated[str | None, "Optional short title after the edit is applied."] = None
    note_type: Annotated[NodeType, "The final node type after the edit."]
    parent_id: Annotated[
        str | None,
        "The parent folder/root note id after the edit, if any."
    ] = None

    def to_compact_repr(self) -> str:
        """Return the edited note metadata in a compact, history-safe form."""
        label = f' "{self.label}"' if self.label else ""
        return f'edited {self.note_type} note_id="{self.note_id}"{label}'


class GetNoteOutput(BaseModel):
    """Output from get note tool."""

    type: Literal["get_note"] = "get_note"
    note_id: Annotated[str, "The unique id of the fetched note."]
    graph_uid: Annotated[str, "The board id where the note belongs."]
    label: Annotated[str | None, "Optional short title currently stored on the note."] = None
    content: Annotated[str, "The current markdown body of the note."]
    note_type: Annotated[NodeType, "The current node type of the note."]
    parent_id: Annotated[
        str | None,
        "The current parent folder/root note id, if any."
    ] = None

    def to_compact_repr(self) -> str:
        """Return the fetched note metadata in a compact, history-safe form."""
        label = f' "{self.label}"' if self.label else ""
        return f'read {self.note_type} note_id="{self.note_id}"{label}'


class LinkNotesOutput(BaseModel):
    """Output from link notes tool."""

    type: Literal["link_notes"] = "link_notes"
    link_id: Annotated[str, "The unique id of the newly created link."]
    source_id: Annotated[str, "The note id the link originates from."]
    target_id: Annotated[str, "The note id the link points to."]
    graph_uid: Annotated[str, "The board id where the link belongs."]
    label: Annotated[str | None, "Optional short label rendered on the edge."] = None

    def to_compact_repr(self) -> str:
        """Return the new link metadata in a compact, history-safe form."""
        label = f' "{self.label}"' if self.label else ""
        return f'linked {self.source_id} -> {self.target_id} link_id="{self.link_id}"{label}'


class MemorySearchOutput(BaseModel):
    """Output from memory search tool."""

    type: Literal["memory_search"] = "memory_search"
    answer: str = ""
    references: list[RefAnnotation] = []

    def __str__(self) -> str:
        """To string method."""
        if self.answer:
            return self.answer

        # TODO: Voir pr document_label plus tard
        formatted = "Memory search Results:\n\n"
        for reference in self.references:
            if reference.label or reference.content:
                note_id = reference.ref_id
                url = f"/{reference.ref_type}/{note_id[:5]}"
                formatted += f"\n<Source\n  id=\"{note_id}\"\n  url=\"{url}\""
                if reference.label:
                    formatted += f"\n  label=\"{reference.label}\""
                formatted += (
                    f"\n  type=\"{reference.ref_type}\""
                    "\n>"
                    f"\n{reference.content or ""}\n"
                    "\n</Source>\n"
                )
        return formatted

    def to_compact_repr(self) -> str:
        """Return whether an answer or references were found."""
        if self.answer:
            return f"answered with {len(self.references)} references"
        return f"{len(self.references)} references"


class ImageGenerationOutput(BaseModel):
    """Output from image generation tool."""

    type: Literal["image_generation"] = "image_generation"
    image_urls: list[str] = []

    def to_compact_repr(self) -> str:
        """Return the number of generated images."""
        return f"{len(self.image_urls)} images"


class ChangeKindOutput(BaseModel):
    """Output from the change-note-kind tool."""

    type: Literal["change_note_kind"] = "change_note_kind"
    note_id: Annotated[str, "The note id whose kind was changed."]
    graph_uid: Annotated[str, "The board id where the note belongs."]
    kind: Annotated[str, "The new research kind (question/finding/…)."]

    def to_compact_repr(self) -> str:
        """Return a compact history-safe summary."""
        return f'kind={self.kind} note_id="{self.note_id}"'


class ReparentNoteOutput(BaseModel):
    """Output from the reparent-note tool."""

    type: Literal["reparent_note"] = "reparent_note"
    note_id: Annotated[str, "The note id that was moved."]
    graph_uid: Annotated[str, "The board id where the note belongs."]
    parent_id: Annotated[str | None, "The new parent note id, or None for board root."] = None

    def to_compact_repr(self) -> str:
        """Return a compact history-safe summary."""
        return f'reparented note_id="{self.note_id}" under {self.parent_id}'


class DeleteSubtreeOutput(BaseModel):
    """Output from the delete-subtree tool."""

    type: Literal["delete_subtree"] = "delete_subtree"
    graph_uid: Annotated[str, "The board id where the subtree lived."]
    deleted_nodes: Annotated[int, "Number of nodes deleted (root + descendants)."]
    deleted_edges: Annotated[int, "Number of edges deleted."] = 0

    def to_compact_repr(self) -> str:
        """Return a compact history-safe summary."""
        return f'deleted subtree: {self.deleted_nodes} nodes, {self.deleted_edges} edges'


class MergeNotesOutput(BaseModel):
    """Output from the merge-notes tool."""

    type: Literal["merge_notes"] = "merge_notes"
    target_id: Annotated[str, "The note id that absorbed the others."]
    graph_uid: Annotated[str, "The board id where the notes belonged."]
    absorbed: Annotated[int, "Number of notes folded into the target."]

    def to_compact_repr(self) -> str:
        """Return a compact history-safe summary."""
        return f'merged {self.absorbed} into note_id="{self.target_id}"'


class SplitNoteOutput(BaseModel):
    """Output from the split-note tool."""

    type: Literal["split_note"] = "split_note"
    graph_uid: Annotated[str, "The board id where the note belonged."]
    created_ids: Annotated[list[str], "The new note ids created from the split."]
    original_deleted: Annotated[bool, "Whether the original note was deleted."] = True

    def to_compact_repr(self) -> str:
        """Return a compact history-safe summary."""
        return f'split into {len(self.created_ids)} notes'


class RelayoutOutput(BaseModel):
    """Output from the relayout tool."""

    type: Literal["relayout_board"] = "relayout_board"
    graph_uid: Annotated[str, "The board id that was relaid out."]
    moved: Annotated[int, "Number of nodes moved."]
    mode: Annotated[str, "Layout mode used (default/research)."] = "default"

    def to_compact_repr(self) -> str:
        """Return a compact history-safe summary."""
        return f'relayout {self.mode}: moved {self.moved}'


type ToolOutput = Union[
    str,
    CodeInterpreterOutput,
    WriteNoteOutput,
    CreateNoteOutput,
    EditNoteOutput,
    GetNoteOutput,
    LinkNotesOutput,
    WebSearchOutput,
    MemorySearchOutput,
    NotifyOutput,
    MapifyTheme,
    TopicTracker,
    NewsfeedOutput,
    SchemaOutput,
    TranslateOutput,
    TopicIllustratorOutput,
    ImageDescriptionOutput,
    DisplayStockWidgetOutput,
    DisplayWeatherWidgetOutput,
    DisplayImageSearchWidgetOutput,
    ImageGenerationOutput,
    ChangeKindOutput,
    ReparentNoteOutput,
    DeleteSubtreeOutput,
    MergeNotesOutput,
    SplitNoteOutput,
    RelayoutOutput,
    DrawnGraph,
]
