"""Research node metadata conventions for iterative board research.

Every research node should carry a stable kind + phase so reframe/expand
can remap without losing lineage. Metadata is embedded in note content
(human-readable) because canvas notes do not yet have a first-class
research schema column.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

VALID_KINDS = frozenset({
    "question",
    "workstream",
    "source",
    "evidence",
    "finding",
    "hypothesis",
    "contradiction",
    "unknown",
    "alternative",
    "decision",
    "summary",
    "status",
    "note",
})


VALID_PHASES = frozenset({
    "explore",
    "reframe",
    "expand",
    "critique",
    "manual",
})


class ResearchMeta(BaseModel):
    """Structured research metadata stamped onto integration nodes."""

    kind: str = "note"
    phase: str = "explore"
    session_id: str | None = None
    parent_workstream_id: str | None = None
    taxonomy: str | None = None
    tags: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    citations: list[dict[str, str]] = Field(default_factory=list)
    brand: str | None = None
    campaign: str | None = None
    year: str | None = None

    def normalized_kind(self) -> str:
        """Return a known kind or fall back to note."""
        k = (self.kind or "note").strip().lower()
        return k if k in VALID_KINDS else "note"

    def normalized_phase(self) -> str:
        """Return a known phase or explore."""
        p = (self.phase or "explore").strip().lower()
        return p if p in VALID_PHASES else "explore"


def merge_research_metadata(
    kind: str,
    metadata: dict[str, Any] | None,
    *,
    phase: str | None = None,
    session_id: str | None = None,
) -> ResearchMeta:
    """Merge free-form metadata into a ResearchMeta with defaults."""
    raw = dict(metadata or {})
    # Flatten nested research block if present
    nested = raw.pop("research", None)
    if isinstance(nested, dict):
        for k, v in nested.items():
            raw.setdefault(k, v)

    return ResearchMeta(
        kind=raw.get("kind") or kind or "note",
        phase=raw.get("phase") or phase or "explore",
        session_id=raw.get("session_id") or session_id,
        parent_workstream_id=raw.get("parent_workstream_id"),
        taxonomy=raw.get("taxonomy"),
        tags=list(raw.get("tags") or []),
        confidence=raw.get("confidence"),
        citations=list(raw.get("citations") or []),
        brand=raw.get("brand"),
        campaign=raw.get("campaign"),
        year=str(raw["year"]) if raw.get("year") is not None else None,
    )


def format_research_meta_block(meta: ResearchMeta) -> str:
    """Render a compact, skimmable metadata chip block for note content."""
    chips: list[str] = []
    chips.append(f"`{meta.normalized_phase()}`")
    if meta.taxonomy:
        chips.append(meta.taxonomy)
    if meta.tags:
        chips.append(", ".join(meta.tags[:4]))
    if meta.confidence is not None:
        chips.append(f"conf {meta.confidence:.0%}")

    lines: list[str] = []
    if chips:
        lines.append(" · ".join(chips))

    facts: list[str] = []
    if meta.brand:
        facts.append(f"**Brand** {meta.brand}")
    if meta.campaign:
        facts.append(f"**Campaign** {meta.campaign}")
    if meta.year:
        facts.append(f"**Year** {meta.year}")
    if facts:
        lines.append("  \n".join(facts))

    if meta.citations:
        lines.append("")
        lines.append("**Sources**")
        for c in meta.citations[:6]:
            title = (c.get("title") or c.get("url") or "link").strip()
            url = (c.get("url") or "").strip()
            if url:
                lines.append(f"- [{title[:80]}]({url})")
            else:
                lines.append(f"- {title[:80]}")
    return "\n".join(lines).strip()


def stamp_content(content: str | None, meta: ResearchMeta) -> str:
    """Prefix content with research metadata block (idempotent-ish)."""
    from topix.integrations.research_style import pretty_body

    body = (content or "").strip()
    if body.startswith("**Kind:**") or body.startswith("### "):
        return body
    block = format_research_meta_block(meta)
    return pretty_body(meta.normalized_kind(), body, block)
