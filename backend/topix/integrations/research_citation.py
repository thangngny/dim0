"""Citation helpers for source reliability (sub-project C).

Pure functions to dedupe source/evidence nodes by URL so an agent does not
re-create a source that is already on the board. URL verification and
click-through rendering live elsewhere (canvas / note media).
"""

from __future__ import annotations

import re
from typing import Any


# Markdown [text](url) and bare http(s) URLs.
_MD_LINK = re.compile(r"\[[^\]]*\]\((https?://[^\s)]+)\)")
_BARE_URL = re.compile(r"(?<![\(\]])(https?://[^\s)\]]+)")


def extract_urls(text: str | None) -> set[str]:
    """Return the set of http(s) URLs found in `text` (markdown + bare)."""
    if not text:
        return set()
    urls: set[str] = set(_MD_LINK.findall(text))
    for u in _BARE_URL.findall(text):
        urls.add(u)
    # Strip trailing punctuation that regexes sometimes capture.
    return {u.rstrip(".,);:") for u in urls}


def _node_urls(node: dict[str, Any]) -> set[str]:
    """Pull URLs from a graph node's label + content."""
    urls: set[str] = set()
    for key in ("content", "label"):
        val = node.get(key)
        if isinstance(val, str):
            urls |= extract_urls(val)
        elif isinstance(val, dict):
            md = val.get("markdown")
            if isinstance(md, str):
                urls |= extract_urls(md)
    return urls


def build_existing_url_index(nodes: list[dict[str, Any]]) -> dict[str, str]:
    """Map every source/evidence URL already on the board to its node id.

    Only `source` and `evidence` kinds are indexed (findings cite, they don't
    dedupe). Later URLs win on collision (kept simple; collisions are rare).
    """
    index: dict[str, str] = {}
    for n in nodes or []:
        if not isinstance(n, dict):
            continue
        kind = str(n.get("kind") or "").lower()
        if kind not in ("source", "evidence"):
            continue
        nid = n.get("id")
        if not nid:
            continue
        for url in _node_urls(n):
            index[url] = nid
    return index


def plan_dedup(
    node_inputs: list[dict[str, Any]],
    existing_index: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Split new nodes into those to create vs those reusing an existing id.

    Only `source`/`evidence` nodes carrying a `metadata.url` that already exists
    on the board are deduped; everything else passes through to creation.
    Returns (nodes_to_create, reuse_map) where reuse_map is client_ref -> node_id.
    """
    to_create: list[dict[str, Any]] = []
    reuse: dict[str, str] = {}
    for n in node_inputs or []:
        kind = str((n.get("kind") or "note")).lower()
        url = ""
        meta = n.get("metadata") or {}
        if isinstance(meta, dict):
            url = str(meta.get("url") or "").strip()
        if kind in ("source", "evidence") and url and url in existing_index:
            reuse[n.get("client_ref")] = existing_index[url]
            continue
        to_create.append(n)
    return to_create, reuse