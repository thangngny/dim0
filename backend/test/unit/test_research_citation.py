"""Unit tests for source dedup-by-URL (sub-project C)."""
from topix.integrations.research_citation import (
    build_existing_url_index,
    extract_urls,
    plan_dedup,
)


def test_extract_urls_finds_markdown_and_raw():
    """extract_urls pulls both markdown-link and bare http(s) URLs."""
    text = "See [OpenAI](https://openai.com/research) and https://example.com/a"
    urls = extract_urls(text)
    assert "https://openai.com/research" in urls
    assert "https://example.com/a" in urls


def test_extract_urls_ignores_non_http():
    """mailto / javascript / ftp are not citations."""
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