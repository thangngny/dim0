"""Hierarchical canvas layout for research graphs.

Places nodes in readable tiers so a board scans top→bottom:

  Question
  Workstreams (row)
  Findings / Decisions
  Sources / Evidence
  Unknown / Contradiction
  Summary (bottom)

Only repositions the provided node ids (and optionally their edges).
"""

from __future__ import annotations

import logging
import re

from collections import defaultdict

from topix.collab.agent_bridge import AgentBoardBridge
from topix.datatypes.note.link import Link
from topix.datatypes.note.style import LinkStyle, PathStyle, StrokeStyle
from topix.datatypes.property import PositionProperty
from topix.datatypes.resource import RichText
from topix.store.graph import GraphStore
from topix.utils.colors import TAILWIND_200_RAW, adapt_tailwind_color

logger = logging.getLogger(__name__)


# Vertical tiers (smaller = higher on canvas)
_KIND_TIER: dict[str, int] = {
    "question": 0,
    "workstream": 1,
    "finding": 2,
    "hypothesis": 2,
    "decision": 2,
    "alternative": 2,
    "source": 3,
    "evidence": 3,
    "unknown": 4,
    "contradiction": 4,
    "status": 4,
    "note": 4,
    "summary": 5,
}

_TIER_GAP_Y = 280.0
_NODE_GAP_X = 80.0
_ORIGIN_X = 80.0
_ORIGIN_Y = 80.0

# Edge relation → stroke family (adapted)
_RELATION_STROKE: dict[str, str] = {
    "investigates": "violet",
    "supports": "emerald",
    "contradicts": "rose",
    "derived_from": "amber",
    "leads_to": "sky",
    "summarizes": "indigo",
    "depends_on": "slate",
    "blocks": "red",
    "produces": "teal",
    "supersedes": "stone",
}


def _infer_kind_from_note(label: str | None, content: str | None) -> str:
    """Best-effort kind detection from title/body (for layout)."""
    text = f"{label or ''}\n{content or ''}".lower()
    # Content header "### emoji ShortLabel"
    m = re.search(r"###\s*.*?\b(question|workstream|source|evidence|finding|"
                  r"hypothesis|contradiction|unknown|alternative|decision|"
                  r"summary|status|note)\b", text)
    if m:
        return m.group(1)
    # emoji heuristics
    for kind, keys in (
        ("question", ("❓", "question")),
        ("workstream", ("🧭", "🔍", "workstream")),
        ("source", ("📄", "source")),
        ("evidence", ("🔗", "🔬", "evidence")),
        ("finding", ("💡", "finding")),
        ("contradiction", ("⚡", "⚠️", "contradiction")),
        ("unknown", ("❔", "unknown")),
        ("decision", ("✅", "decision")),
        ("summary", ("📋", "summary")),
        ("hypothesis", ("🧪", "hypothesis")),
    ):
        if any(k in text for k in keys):
            return kind
    return "note"


def _stroke(family: str) -> str:
    raw = TAILWIND_200_RAW.get(family, TAILWIND_200_RAW["slate"])
    return adapt_tailwind_color(raw, 400)


def style_for_relation(relation: str | None) -> LinkStyle:
    """Return a slightly styled bezier arrow for a research relation label."""
    from topix.datatypes.note.style import FontSize

    key = (relation or "").strip().lower().replace(" ", "_")
    family = _RELATION_STROKE.get(key, "stone")
    return LinkStyle(
        stroke_color=_stroke(family),
        stroke_width=2.0,
        stroke_style=StrokeStyle.SOLID,
        roughness=0.4,
        path_style=PathStyle.BEZIER,
        font_size=FontSize.S,
    )


async def apply_research_layout(  # noqa: C901  # layout dispatch is branchy
    *,
    graph_store: GraphStore,
    bridge: AgentBoardBridge,
    board_id: str,
    created_ids: list[str],
    kind_by_id: dict[str, str] | None = None,
) -> list[str]:
    """Reposition created research nodes into hierarchical tiers.

    Returns list of moved node ids.
    """
    if not created_ids:
        return []

    notes = await graph_store.get_nodes(created_ids)
    if not notes:
        return []

    kind_by_id = dict(kind_by_id or {})
    # Group by tier
    tiers: dict[int, list] = defaultdict(list)
    for note in notes:
        if note.deleted_at is not None:
            continue
        kind = kind_by_id.get(note.id)
        if not kind:
            lab = note.label.markdown if note.label else None
            cont = note.content.markdown if note.content else None
            kind = _infer_kind_from_note(lab, cont)
        tier = _KIND_TIER.get(kind, 4)
        tiers[tier].append(note)

    # Stack below existing content so a second pass doesn't overlap the first.
    existing_graph = await graph_store.get_graph(board_id)
    existing_nodes = []
    if existing_graph:
        for n in existing_graph.nodes:
            pos = getattr(n.properties, "node_position", None)
            py = getattr(getattr(pos, "position", None), "y", None) if pos else None
            sz = getattr(n.properties, "node_size", None)
            ph = getattr(getattr(sz, "size", None), "height", None) if sz else None
            existing_nodes.append({
                "id": n.id,
                "deleted": n.deleted_at is not None,
                "y": float(py) if py is not None else None,
                "h": float(ph) if ph is not None else None,
            })
    from topix.integrations.research_layout_math import compute_layout_origin_y
    origin_y = compute_layout_origin_y(existing_nodes, created_ids)

    moved: list[str] = []
    patches: list[tuple[str, dict]] = []

    for tier in sorted(tiers.keys()):
        row = tiers[tier]
        # Sort by label for stable left→right
        row.sort(key=lambda n: (n.label.markdown if n.label else n.id))
        widths = [
            float(n.properties.node_size.size.width or 320)
            for n in row
        ]
        total_w = sum(widths) + _NODE_GAP_X * max(0, len(row) - 1)
        # Center row around a soft origin
        x = _ORIGIN_X + max(0.0, (1400.0 - total_w) / 2.0)
        y = origin_y + tier * _TIER_GAP_Y
        for note, w in zip(row, widths):
            patches.append((
                note.id,
                {
                    "properties": {
                        "node_position": PositionProperty(
                            position=PositionProperty.Position(x=x, y=y),
                        ).model_dump(),
                    }
                },
            ))
            x += w + _NODE_GAP_X
            moved.append(note.id)

    # Apply patches one-by-one (bridge API) so live peers get node.update.
    for note_id, data in patches:
        try:
            updated = await bridge.patch_note(
                board_id=board_id,
                node_id=note_id,
                data=data,
                user_uid=None,
            )
            if updated is None:
                logger.warning("research_layout: patch returned None for %s", note_id)
        except Exception:
            logger.exception("research_layout: failed to move note %s", note_id)

    logger.info(
        "research_layout: moved %s/%s notes on board=%s",
        len(patches),
        len(created_ids),
        board_id,
    )
    return moved


def decorate_research_link(
    *,
    source_id: str,
    target_id: str,
    board_id: str,
    relation: str | None,
) -> Link:
    """Build a Link with research-friendly arrow styling + short label."""
    label = (relation or "").strip()
    # Prettify relation for canvas
    pretty = label.replace("_", " ")
    style = style_for_relation(label)
    return Link(
        source=source_id,
        target=target_id,
        graph_uid=board_id,
        label=RichText(markdown=pretty) if pretty else None,
        style=style,
    )
