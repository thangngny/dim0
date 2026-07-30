"""Citation helpers for source reliability (sub-project C).

Pure functions to dedupe source/evidence nodes by URL so an agent does not
re-create a source that is already on the board. URL verification and
click-through rendering live elsewhere (canvas / note media).

Also provides source-reliability grading (domain trust + recency +
corroboration) as pure helpers, ready to wire into source-node metadata or
the agent prompt. These do not touch the research run loop.
"""

from __future__ import annotations

import re

from typing import Any
from urllib.parse import urlsplit

# Markdown [text](url) and bare http(s) URLs.
_MD_LINK = re.compile(r"\[[^\]]*\]\((https?://[^\s)]+)\)")
_BARE_URL = re.compile(r"(?<![\(\]])(https?://[^\s)\]]+)")

# TLDs that imply editorial/institutional oversight → trusted baseline.
DEFAULT_TRUSTED_TLDS = frozenset({".gov", ".edu", ".mil"})
# Domains an operator may block (content farms / UGC aggregators). Conservative
# default — extend via the ``blocklist`` argument rather than hardcoding opinions.
DEFAULT_BLOCKLIST = frozenset[str]()
# A 4-digit year in the plausible modern range (1990–2099).
_YEAR = re.compile(r"\b(19|20)\d{2}\b")


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
        if not isinstance(nid, str) or not nid:
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
            cref = n.get("client_ref")
            if isinstance(cref, str):
                reuse[cref] = existing_index[url]
            continue
        to_create.append(n)
    return to_create, reuse


def _trust_score(
    domain: str | None,
    *,
    trusted_tlds: frozenset[str],
    blocklist: frozenset[str],
    allowlist: frozenset[str],
) -> tuple[float, list[str]]:
    """Return (trust score in [0,1], reasons) for a source domain.

    A domain is trusted when it is explicitly allowlisted or when any of its
    labels matches a trusted TLD — this covers both ``example.gov`` and
    country-TLD variants like ``stats.gov.vn``.
    """
    if domain and domain in blocklist:
        return 0.0, [f"domain '{domain}' is blocklisted"]
    if domain and domain in allowlist:
        return 1.0, ["trusted domain (institutional/allowlisted)"]
    if domain:
        labels = set(domain.split("."))
        trusted_labels = {t.lstrip(".") for t in trusted_tlds}
        if labels & trusted_labels:
            return 1.0, ["trusted domain (institutional/allowlisted)"]
    return 0.6, []


def _grade_from_score(score: float, blocked: bool) -> str:
    """Map a reliability score to a coarse grade label."""
    if blocked or score < 0.4:
        return "low"
    return "high" if score >= 0.7 else "medium"


def extract_domain(url: str | None) -> str | None:
    """Return the registrable host (lowercased, ``www.`` stripped) or None.

    Uses ``urlsplit`` and strips a leading ``www.``. Does not attempt full PSL
    eTLD+1 extraction — the host is enough for TLD/blocklist checks.
    """
    if not url:
        return None
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return None
    if not host:
        return None
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def extract_year(text: str | None) -> int | None:
    """Return the most recent plausible year (1990–2099) mentioned in ``text``."""
    if not text:
        return None
    years = [int(m.group(0)) for m in _YEAR.finditer(text)]
    if not years:
        return None
    return max(years)


def page_age_score(year: int | None, current_year: int) -> float:
    """Recency score in [0.1, 1.0]: 1.0 for ``current_year``, decaying ~0.15/yr.

    Unknown years score 0.4 (neutral-ish) — a missing date is not evidence of
    staleness, but it should not earn full recency credit either.
    """
    if year is None:
        return 0.4
    if year >= current_year:
        return 1.0
    return max(0.1, 1.0 - 0.15 * (current_year - year))


def corroboration_count(source_url: str, finding_nodes: list[dict[str, Any]]) -> int:
    """Count finding nodes whose text cites ``source_url``."""
    if not source_url or not finding_nodes:
        return 0
    count = 0
    for fn in finding_nodes:
        if not isinstance(fn, dict):
            continue
        urls = _node_urls(fn)
        if source_url in urls:
            count += 1
    return count


def grade_source_reliability(
    source_node: dict[str, Any],
    finding_nodes: list[dict[str, Any]] | None = None,
    *,
    current_year: int = 2026,
    trusted_tlds: frozenset[str] = DEFAULT_TRUSTED_TLDS,
    blocklist: frozenset[str] = DEFAULT_BLOCKLIST,
    allowlist: frozenset[str] = DEFAULT_BLOCKLIST,
) -> dict[str, Any]:
    """Grade a source node's reliability from domain trust + recency + corroboration.

    Returns ``{grade, score, domain, year, corroboration, blocked, reasons}``
    where ``grade`` is ``"high" | "medium" | "low"`` and ``score`` is in [0, 1].
    Pure and side-effect free; safe to call during plan processing or rendering.
    """
    meta = source_node.get("metadata") or {}
    url = ""
    if isinstance(meta, dict):
        url = str(meta.get("url") or "").strip()
    if not url:
        url = next(iter(_node_urls(source_node)), "")
    domain = extract_domain(url)
    text = ""
    for key in ("content", "label"):
        val = source_node.get(key)
        if isinstance(val, str):
            text += " " + val
        elif isinstance(val, dict):
            md = val.get("markdown")
            if isinstance(md, str):
                text += " " + md
    year = extract_year(text)
    corroboration = corroboration_count(url, finding_nodes or [])
    blocked = bool(domain and domain in blocklist)

    trust, trust_reasons = _trust_score(
        domain, trusted_tlds=trusted_tlds, blocklist=blocklist, allowlist=allowlist)
    recency = page_age_score(year, current_year)
    corr_norm = min(1.0, corroboration * 0.5)

    reasons = list(trust_reasons)
    reasons.append(f"published {year}" if year is not None else "no date found")
    if corroboration > 0:
        reasons.append(f"corroborated by {corroboration} finding(s)")

    score = round(0.4 * trust + 0.3 * recency + 0.3 * corr_norm, 3)
    grade = _grade_from_score(score, blocked)

    return {
        "grade": grade,
        "score": score,
        "domain": domain,
        "year": year,
        "corroboration": corroboration,
        "blocked": blocked,
        "reasons": reasons,
    }
