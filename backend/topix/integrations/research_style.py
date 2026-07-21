"""Visual design system for research-graph nodes on the Dim0 canvas.

Maps semantic kinds → shape + paper-adapted color so graphs read as a
coherent diagram (not random sticky notes). Keep colors from the same
adapted Tailwind-200 palette the canvas already renders.
"""

from __future__ import annotations

from dataclasses import dataclass

from topix.datatypes.note.style import (
    FillStyle,
    FontFamily,
    FontSize,
    NodeType,
    StrokeStyle,
    Style,
    TextAlign,
)
from topix.utils.colors import TAILWIND_200_RAW, adapt_tailwind_color


def _c(family: str, shade: int = 200) -> str:
    """Resolve a paper-adapted Tailwind family/shade hex."""
    raw = TAILWIND_200_RAW.get(family, TAILWIND_200_RAW["blue"])
    # For non-200 we still warm the 200 ramp then darken lightly for stroke
    # via a second adapt call when shade != 200 is needed for borders.
    if shade == 200:
        return adapt_tailwind_color(raw, 200)
    return adapt_tailwind_color(raw, shade)


@dataclass(frozen=True)
class KindVisual:
    """Visual recipe for one research kind."""

    shape: NodeType
    fill_family: str
    emoji: str
    short_label: str
    roundness: float = 2.0
    font_size: FontSize = FontSize.M
    stroke_family: str | None = None


# Semantic palette — stable, high-contrast enough on parchment theme.
KIND_VISUALS: dict[str, KindVisual] = {
    "question": KindVisual(
        shape=NodeType.LAYERED_CIRCLE,
        fill_family="violet",
        emoji="❓",
        short_label="Question",
        roundness=3.0,
        font_size=FontSize.L,
    ),
    "workstream": KindVisual(
        shape=NodeType.CAPSULE,
        fill_family="sky",
        emoji="🧭",
        short_label="Workstream",
        roundness=3.0,
        font_size=FontSize.M,
    ),
    "source": KindVisual(
        shape=NodeType.RECTANGLE,
        fill_family="amber",
        emoji="📄",
        short_label="Source",
        roundness=1.0,
    ),
    "evidence": KindVisual(
        shape=NodeType.RECTANGLE,
        fill_family="orange",
        emoji="🔗",
        short_label="Evidence",
        roundness=1.0,
    ),
    "finding": KindVisual(
        shape=NodeType.SOFT_DIAMOND,
        fill_family="emerald",
        emoji="💡",
        short_label="Finding",
        roundness=2.0,
    ),
    "hypothesis": KindVisual(
        shape=NodeType.DIAMOND,
        fill_family="teal",
        emoji="🧪",
        short_label="Hypothesis",
    ),
    "contradiction": KindVisual(
        shape=NodeType.LAYERED_DIAMOND,
        fill_family="rose",
        emoji="⚡",
        short_label="Contradiction",
        stroke_family="red",
    ),
    "unknown": KindVisual(
        shape=NodeType.THOUGHT_CLOUD,
        fill_family="stone",
        emoji="❔",
        short_label="Unknown",
        roundness=3.0,
    ),
    "alternative": KindVisual(
        shape=NodeType.SOFT_DIAMOND,
        fill_family="fuchsia",
        emoji="🔀",
        short_label="Alternative",
    ),
    "decision": KindVisual(
        shape=NodeType.LAYERED_RECTANGLE,
        fill_family="green",
        emoji="✅",
        short_label="Decision",
        roundness=1.0,
        font_size=FontSize.M,
        stroke_family="emerald",
    ),
    "summary": KindVisual(
        shape=NodeType.LAYERED_RECTANGLE,
        fill_family="indigo",
        emoji="📋",
        short_label="Summary",
        roundness=2.0,
        font_size=FontSize.L,
    ),
    "status": KindVisual(
        shape=NodeType.TAG,
        fill_family="slate",
        emoji="📊",
        short_label="Status",
        roundness=3.0,
    ),
    "note": KindVisual(
        shape=NodeType.RECTANGLE,
        fill_family="blue",
        emoji="📝",
        short_label="Note",
        roundness=1.0,
    ),
}


def get_kind_visual(kind: str) -> KindVisual:
    """Look up visual recipe; fall back to note."""
    return KIND_VISUALS.get((kind or "note").lower(), KIND_VISUALS["note"])


def build_research_style(kind: str) -> Style:
    """Build a canvas Style for a research kind (shape + fill + stroke)."""
    vis = get_kind_visual(kind)
    fill = _c(vis.fill_family, 200)
    stroke = "#00000000"
    if vis.stroke_family:
        # Slightly stronger border for emphasis kinds
        stroke = _c(vis.stroke_family, 300) if vis.stroke_family in TAILWIND_200_RAW else _c("slate", 300)

    return Style(
        type=vis.shape,
        background_color=fill,
        stroke_color=stroke,
        stroke_width=2.0 if vis.stroke_family else 1.5,
        stroke_style=StrokeStyle.SOLID,
        fill_style=FillStyle.SOLID,
        roughness=0.35,
        roundness=vis.roundness,
        opacity=100,
        font_size=vis.font_size,
        font_family=FontFamily.SANS_SERIF,
        text_align=TextAlign.CENTER,
        text_color="#1c1917",
    )


def pretty_title(kind: str, title: str | None) -> str:
    """Build a clean label: one emoji + short title (no double emoji)."""
    vis = get_kind_visual(kind)
    raw = (title or vis.short_label).strip()
    # Strip leading emoji/kind noise from model output
    for em in (vis.emoji, "❓", "🔍", "📄", "🔬", "💡", "🧪", "⚠️", "❔", "🔀", "✅", "📋", "📊", "🧭", "⚡", "🔗", "📝"):
        if raw.startswith(em):
            raw = raw[len(em):].strip()
    # Drop duplicated "Kind: x" prefixes
    lower = raw.lower()
    for prefix in ("kind:", "workstream:", "finding:", "source:", "question:"):
        if lower.startswith(prefix):
            raw = raw[len(prefix):].strip()
            lower = raw.lower()
    # Collapse double spaces / truncate for canvas readability
    raw = " ".join(raw.split())
    if len(raw) > 72:
        raw = raw[:69].rstrip() + "…"
    return f"{vis.emoji} {raw}".strip()


def pretty_body(kind: str, content: str | None, meta_block: str) -> str:
    """Format body markdown for skimmable cards on canvas."""
    vis = get_kind_visual(kind)
    body = (content or "").strip()
    # Avoid double-stamping if body already starts with kind header
    if body.startswith("**Kind:**") or body.startswith("### "):
        return body
    header = f"### {vis.emoji} {vis.short_label}"
    parts = [header]
    if meta_block:
        parts.append(meta_block)
    if body:
        parts.append(body)
    return "\n\n".join(parts)
