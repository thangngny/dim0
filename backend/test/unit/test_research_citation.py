"""Unit tests for source dedup-by-URL and reliability grading (sub-project C)."""
from topix.integrations.research_citation import (
    build_existing_url_index,
    corroboration_count,
    extract_domain,
    extract_urls,
    extract_year,
    grade_source_reliability,
    page_age_score,
    plan_dedup,
)


def test_extract_urls_finds_markdown_and_raw():
    """extract_urls pulls both markdown-link and bare http(s) URLs."""
    text = "See [OpenAI](https://openai.com/research) and https://example.com/a"
    urls = extract_urls(text)
    assert "https://openai.com/research" in urls
    assert "https://example.com/a" in urls


def test_extract_urls_ignores_non_http():
    """Mailto / javascript / ftp are not citations."""
    assert extract_urls("mailto:a@b.com ftp://x") == set()


def test_build_index_maps_url_to_node_id_for_source_evidence():
    """Only source/evidence kinds contribute to the dedupe index."""
    nodes = [
        {"id": "n1", "kind": "source",
         "content": "x [A](https://a.com/p)"},
        {"id": "n2", "kind": "finding",
         "content": "[B](https://b.com/p)"},
        {"id": "n3", "kind": "evidence",
         "content": "https://c.com/p"},
    ]
    idx = build_existing_url_index(nodes)
    assert idx["https://a.com/p"] == "n1"
    assert idx["https://c.com/p"] == "n3"
    # finding is not a source/evidence → not indexed
    assert "https://b.com/p" not in idx


def test_plan_dedup_reuses_existing_for_matching_url():
    """A new source whose URL already exists reuses the existing node id."""
    new = [
        {"client_ref": "s1", "kind": "source", "metadata": {"url": "https://a.com/p"}},
        {"client_ref": "s2", "kind": "source", "metadata": {"url": "https://new.com"}},
        {"client_ref": "f1", "kind": "finding", "metadata": {"url": "https://a.com/p"}},
    ]
    idx = {"https://a.com/p": "n1"}
    to_create, reuse = plan_dedup(new, idx)
    # s1 reuses n1; s2 created; f1 is a finding (not deduped) → created
    refs_to_create = {n["client_ref"] for n in to_create}
    assert "s1" not in refs_to_create
    assert "s2" in refs_to_create
    assert "f1" in refs_to_create
    assert reuse["s1"] == "n1"
    assert "s2" not in reuse
    assert "f1" not in reuse


def test_plan_dedup_passthrough_when_no_url():
    """Source nodes without a URL are always created (nothing to dedupe against)."""
    new = [{"client_ref": "s1", "kind": "source", "metadata": {}}]
    to_create, reuse = plan_dedup(new, {})
    assert to_create == new
    assert reuse == {}


# --- reliability grading -----------------------------------------------------


def test_extract_domain_strips_www_and_lowercases():
    """extract_domain lowercases the host and drops a leading www."""
    assert extract_domain("https://WWW.Example.com/path") == "example.com"
    assert extract_domain("https://gov.vn/p") == "gov.vn"
    assert extract_domain("not a url") is None
    assert extract_domain(None) is None


def test_extract_year_picks_most_recent():
    """extract_year returns the latest plausible year mentioned."""
    assert extract_year("Data from 2019 and 2024 updates") == 2024
    assert extract_year("no date here") is None
    assert extract_year("year 1899 then 2025") == 2025


def test_page_age_score_decays_with_age():
    """Recency is 1.0 for the current year and decays ~0.15/yr, floored at 0.1."""
    assert page_age_score(2026, 2026) == 1.0
    assert page_age_score(2024, 2026) == 0.7
    assert page_age_score(2010, 2026) == 0.1
    assert page_age_score(None, 2026) == 0.4


def test_corroboration_count_counts_citing_findings():
    """corroboration_count counts findings whose text contains the source URL."""
    findings = [
        {"content": "see https://a.com/p for detail"},
        {"label": {"markdown": "[A](https://a.com/p) says..."}},
        {"content": "no link here"},
    ]
    assert corroboration_count("https://a.com/p", findings) == 2
    assert corroboration_count("https://other.com", findings) == 0
    assert corroboration_count("", findings) == 0


def _source(url: str, year: int | None, content: str = "") -> dict:
    meta = {"url": url} if url else {}
    text = content if content else (f"Report {year}" if year else "undated")
    return {"kind": "source", "metadata": meta, "content": text}


def test_grade_trusted_recent_corroborated_is_high():
    """A .gov source from the current year cited by ≥2 findings grades high."""
    src = _source("https://stats.gov.vn/report", 2026)
    findings = [
        {"content": "per https://stats.gov.vn/report"},
        {"content": "[x](https://stats.gov.vn/report)"},
    ]
    g = grade_source_reliability(src, findings, current_year=2026)
    assert g["grade"] == "high"
    assert g["blocked"] is False
    assert g["corroboration"] == 2
    assert "trusted domain" in " ".join(g["reasons"])


def test_grade_blocklisted_domain_is_low_regardless_of_rest():
    """A blocklisted domain always grades low."""
    src = _source("https://contentfarm.biz/x", 2026)
    findings = [{"content": "https://contentfarm.biz/x"}]
    g = grade_source_reliability(
        src, findings, current_year=2026,
        blocklist=frozenset({"contentfarm.biz"}))
    assert g["grade"] == "low"
    assert g["blocked"] is True


def test_grade_stale_uncorroborated_normal_domain_is_medium_or_low():
    """A normal domain with an old year and no corroboration does not grade high."""
    src = _source("https://blog.example.com/post", 2018, content="post from 2018")
    g = grade_source_reliability(src, [], current_year=2026)
    assert g["grade"] in ("medium", "low")
    assert g["score"] < 0.7


def test_grade_allowlist_overrides_to_trusted():
    """An allowlisted normal domain earns the trusted-domain bonus."""
    src = _source("https://kenresearch.com/ev", 2025)
    g = grade_source_reliability(
        src, [{"content": "https://kenresearch.com/ev"}],
        current_year=2026, allowlist=frozenset({"kenresearch.com"}))
    assert g["grade"] == "high"


def test_grade_missing_date_neutral_recency():
    """A source with no date still gets a usable grade (no crash, neutral recency)."""
    src = _source("https://example.com/p", None)
    g = grade_source_reliability(src, [], current_year=2026)
    assert g["year"] is None
    assert "no date found" in g["reasons"]
    assert g["grade"] in ("low", "medium")
