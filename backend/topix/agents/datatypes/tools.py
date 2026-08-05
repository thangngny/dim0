"""Agent tool names."""

from enum import StrEnum


class AgentToolName(StrEnum):
    """Enumeration for tool names used in the agent manager."""

    ANSWER_REFORMULATE = "answer_reformulate"

    MEMORY_SEARCH = "memory_search"
    WEB_SEARCH = "web_search"
    CODE_INTERPRETER = "code_interpreter"
    WRITE_NOTE = "write_note"
    CREATE_NOTE = "create_note"
    EDIT_NOTE = "edit_note"
    GET_NOTE = "get_note"
    DESCRIBE_IMAGE = "describe_image"
    LINK_NOTES = "link_notes"

    NAVIGATE = "navigate"

    OUTLINE_GENERATOR = "outline_generator"
    WEB_COLLECTOR = "web_collector"
    SYNTHESIZER = "synthesizer"

    # widget tools
    DISPLAY_STOCK_WIDGET = "display_stock_widget"
    DISPLAY_WEATHER_WIDGET = "display_weather_widget"
    DISPLAY_IMAGE_SEARCH_WIDGET = "display_image_search_widget"
    LEARN_GENERATE_HTML_WIDGET = "learn_generate_html_widget"
    LEARN_GENERATE_MINI_APP = "learn_generate_mini_app"
    LEARN_GENERATE_DIAGRAM = "learn_generate_diagram"

    RAW_MESSAGE = "raw_message"

    IMAGE_DESCRIPTION = "image_description"
    TOPIC_ILLUSTRATOR = "topic_illustrator"

    IMAGE_GENERATION = "image_generation"

    CHANGE_NOTE_KIND = "change_note_kind"
    REPARENT_NOTE = "reparent_note"
    DELETE_SUBTREE = "delete_subtree"
    MERGE_NOTES = "merge_notes"
    SPLIT_NOTE = "split_note"
    RELAYOUT_BOARD = "relayout_board"


def to_display_output(tool_name: str) -> bool:
    """Check if the tool is for displaying output."""
    return tool_name in [
        AgentToolName.ANSWER_REFORMULATE,
        AgentToolName.RAW_MESSAGE,
        AgentToolName.SYNTHESIZER,
    ]


tool_descriptions = {
    AgentToolName.ANSWER_REFORMULATE: "Reformulate the answer",
    AgentToolName.MEMORY_SEARCH: "Search the memory",
    AgentToolName.WEB_SEARCH: "Search the web",
    AgentToolName.CODE_INTERPRETER: "Run code",
    AgentToolName.WRITE_NOTE: (
        "Create a new note or fully rewrite an existing note using label, content, and note type"
    ),
    AgentToolName.CREATE_NOTE: "Create a new note using content as the main body and label only as an optional title",
    AgentToolName.EDIT_NOTE: (
        "Apply a targeted text edit to a note field using note_id, field, "
        "a unique substring anchor old, replacement new, and optional replace_all"
    ),
    AgentToolName.GET_NOTE: "Read an existing note by note_id to inspect its current label, content, and note type",
    AgentToolName.DESCRIBE_IMAGE: (
        "Read an image note (screenshot/moodboard/pasted picture) with the "
        "vision model — pass note_id + optional focus question"
    ),
    AgentToolName.LINK_NOTES: (
        "Create a directed arrow from one note to another using source_id and target_id, "
        "with an optional short label on the edge"
    ),
    AgentToolName.RAW_MESSAGE: "Reasoning message",
    AgentToolName.NAVIGATE: "Navigate the web",
    AgentToolName.OUTLINE_GENERATOR: "Generate an outline for research",
    AgentToolName.WEB_COLLECTOR: "Collect web content based on the outline",
    AgentToolName.SYNTHESIZER: "Synthesize a report based on collected content",
    AgentToolName.IMAGE_DESCRIPTION: "Describe an image",
    AgentToolName.TOPIC_ILLUSTRATOR: "Illustrate a topic",
    AgentToolName.DISPLAY_STOCK_WIDGET: "Display a stock widget",
    AgentToolName.DISPLAY_WEATHER_WIDGET: "Display a weather widget",
    AgentToolName.DISPLAY_IMAGE_SEARCH_WIDGET: "Display an image search widget",
    AgentToolName.LEARN_GENERATE_HTML_WIDGET: (
        "Learn how to create visual explainers and interactive "
        "HTML widgets such as charts, flash cards, mini slides, and infographics"
    ),
    AgentToolName.LEARN_GENERATE_MINI_APP: (
        "Learn how to author a sandboxed interactive React mini-app "
        "(counter, todo, calculator, algorithm visualizer) before writing "
        "one with `write_note(note_type=\"mini-app\")`"
    ),
    AgentToolName.LEARN_GENERATE_DIAGRAM: (
        "Learn how to compose a structured multi-note answer (mindmap, "
        "taxonomy, schema, flowchart) — brevity rules per node and when "
        "to mix rectangle / ellipse / diamond shapes — before issuing "
        "the parallel write_note + link_notes calls"
    ),
    AgentToolName.IMAGE_GENERATION: "Generate images based on text prompts",
    AgentToolName.CHANGE_NOTE_KIND: (
        "Change a note's research kind "
        "(question/finding/source/evidence/hypothesis/contradiction/"
        "unknown/alternative/decision/summary). Re-styles shape and color."
    ),
    AgentToolName.REPARENT_NOTE: "Move a note under a different parent note (or to the board root). Rejects cycles.",
    AgentToolName.DELETE_SUBTREE: (
        "Delete a note plus all its descendants and internal edges. "
        "Always confirm=False first to preview, then confirm=True after "
        "the user agrees."
    ),
    AgentToolName.MERGE_NOTES: (
        "Fold several notes into one target note (append content, repoint "
        "edges, delete the rest). confirm=False first, then confirm=True."
    ),
    AgentToolName.SPLIT_NOTE: "Split one note into several sibling notes from content chunks. confirm=False first, then confirm=True.",
    AgentToolName.RELAYOUT_BOARD: "Re-run auto-layout for a branch or the whole board to tidy the graph.",
}
