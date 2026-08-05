"""Best-effort web evidence collection for research prompts.

Tries available search backends (Linkup → Tavily → Perplexity → Exa) based on
env keys. Returns an empty briefing when no key is configured so research
still runs offline (model knowledge only).
"""

from __future__ import annotations

import logging
import os
import re

from typing import Any

logger = logging.getLogger(__name__)


def _available_engines() -> list[str]:
    """Return search engines that have API keys in the environment."""
    engines: list[str] = []
    if os.getenv("LINKUP_API_KEY"):
        engines.append("linkup")
    if os.getenv("TAVILY_API_KEY"):
        engines.append("tavily")
    if os.getenv("PERPLEXITY_API_KEY"):
        engines.append("perplexity")
    if os.getenv("EXA_API_KEY"):
        engines.append("exa")
    return engines


def derive_search_queries(instruction: str, *, language: str = "vi", max_q: int = 4) -> list[str]:
    """Derive a few web search queries from a free-form research instruction."""
    text = re.sub(r"\s+", " ", instruction.strip())
    if not text:
        return []

    queries = [text[:200]]
    # Heuristic expansions for common research patterns
    lower = text.lower()
    if any(k in lower for k in ("bảo hiểm", "insurance", "brand", "thương hiệu", "campaign")):
        queries.append(f"{text[:120]} advertising campaign case study")
        queries.append(f"{text[:80]} emotional storytelling TVC")
    if any(k in lower for k in ("cảm động", "emotional", "hài hước", "humor", "storytelling")):
        queries.append(f"{text[:100]} brand storytelling examples")

    # de-dupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for q in queries:
        qn = q.strip()
        if qn and qn not in seen:
            seen.add(qn)
            out.append(qn)
        if len(out) >= max_q:
            break
    return out


async def _search_one(engine: str, query: str, max_results: int = 5) -> list[dict[str, str]]:
    """Run one engine search; return list of {title,url,snippet}."""
    from topix.agents.datatypes.web_search import WebSearchContextSize

    try:
        if engine == "linkup":
            from topix.agents.websearch.tools import search_linkup
            out = await search_linkup(query, max_results=max_results, search_context_size=WebSearchContextSize.MEDIUM)
        elif engine == "tavily":
            from topix.agents.websearch.tools import search_tavily
            out = await search_tavily(query, max_results=max_results, search_context_size=WebSearchContextSize.MEDIUM)
        elif engine == "perplexity":
            from topix.agents.websearch.tools import search_perplexity
            out = await search_perplexity(query, max_results=max_results, search_context_size=WebSearchContextSize.MEDIUM)
        elif engine == "exa":
            from topix.agents.websearch.tools import search_exa
            out = await search_exa(query, max_results=max_results, search_context_size=WebSearchContextSize.MEDIUM)
        else:
            return []
    except Exception as exc:
        logger.warning("evidence_collect: %s failed for query=%r: %s", engine, query[:80], exc)
        return []

    results: list[dict[str, str]] = []
    for r in (out.search_results or [])[:max_results]:
        results.append({
            "title": (getattr(r, "title", None) or "")[:200],
            "url": (getattr(r, "url", None) or "")[:500],
            "snippet": (getattr(r, "content", None) or "")[:400],
            "engine": engine,
        })
    return results


async def collect_evidence_briefing(
    instruction: str,
    *,
    language: str = "vi",
    max_results_total: int = 12,
) -> dict[str, Any]:
    """Collect web evidence and format a prompt briefing.

    Returns:
        {
          "available": bool,
          "engines": [...],
          "queries": [...],
          "results": [{title,url,snippet,engine}, ...],
          "briefing_text": str,  # inject into Claude prompt
        }

    """
    engines = _available_engines()
    queries = derive_search_queries(instruction, language=language)

    if not engines:
        msg = (
            "WEB_EVIDENCE: none (no LINKUP/TAVILY/PERPLEXITY/EXA API key). "
            "Use carefully labeled internal knowledge; mark confidence lower; "
            "prefer Unknown nodes where evidence is weak."
        )
        return {
            "available": False,
            "engines": [],
            "queries": queries,
            "results": [],
            "briefing_text": msg,
        }

    engine = engines[0]
    all_results: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    for q in queries:
        if len(all_results) >= max_results_total:
            break
        batch = await _search_one(engine, q, max_results=5)
        for item in batch:
            url = item.get("url") or ""
            if url and url in seen_urls:
                continue
            if url:
                seen_urls.add(url)
            all_results.append(item)
            if len(all_results) >= max_results_total:
                break

    if not all_results:
        # try next engines
        for eng in engines[1:]:
            batch = await _search_one(eng, queries[0], max_results=max_results_total)
            for item in batch:
                all_results.append(item)
            if all_results:
                engine = eng
                break

    lines = [
        f"WEB_EVIDENCE (engine={engine}, results={len(all_results)}):",
        "Use these as Source/Evidence nodes with real URLs in citations.",
        "Do not invent URLs. If a claim is not supported, create Unknown or lower confidence.",
        "",
    ]
    for i, r in enumerate(all_results, 1):
        lines.append(f"{i}. {r.get('title') or '(no title)'}")
        lines.append(f"   URL: {r.get('url') or ''}")
        snip = (r.get("snippet") or "").replace("\n", " ")
        if snip:
            lines.append(f"   Snippet: {snip[:280]}")
        lines.append("")

    return {
        "available": bool(all_results),
        "engines": engines,
        "queries": queries,
        "results": all_results,
        "briefing_text": "\n".join(lines),
    }
