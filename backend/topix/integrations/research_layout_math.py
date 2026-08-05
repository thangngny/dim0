"""Pure layout math for research graphs (sub-project D anti-overlap).

Kept separate from `research_layout.py` so it can be unit-tested without
booting the GraphStore/config import chain. `research_layout.py` imports
these helpers.
"""

from __future__ import annotations

# Default top origin (matches research_layout._ORIGIN_Y).
ORIGIN_Y = 80.0


def compute_layout_origin_y(
    existing_nodes: list[dict],
    created_ids: list[str],
    *,
    origin_y: float = ORIGIN_Y,
    gap: float = 120.0,
) -> float:
    """Y at which to start laying out new nodes, stacked below existing content.

    Each research pass used to start at a fixed origin, so a second pass
    landed on top of the first. This returns the bottom of the existing
    non-deleted, non-created nodes (+ gap) so new tiers go underneath.
    """
    created = set(created_ids)
    bottom = float(origin_y)
    for n in existing_nodes or []:
        if not isinstance(n, dict):
            continue
        if n.get("id") in created:
            continue
        if n.get("deleted"):
            continue
        y = n.get("y")
        h = n.get("h") or 220.0
        if y is None:
            continue
        bottom = max(bottom, float(y) + float(h) + gap)
    return bottom
