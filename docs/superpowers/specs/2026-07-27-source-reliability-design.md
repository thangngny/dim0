# Source reliability & citation — dedupe by URL + stronger prompt rules

- **Date:** 2026-07-27 · **Sub-project:** C
- **Pain:** mục 6 (bịa link, link hỏng, không ưu tiên uy tín, không check ngày, trùng nguồn, không hiện provenance, muốn kiểm chứng phải tự tìm), mục 9 (link không click).

## Scope (feasible, low-risk)
1. **Dedupe source/evidence theo URL** trong integration `batch_create`: nếu node mới có `metadata.url` trùng URL đã có trên board → tái dùng node id (không tạo bản sao), edges vẫn resolve. Giết "trùng lặp thông tin".
2. **Mạnh hoá `graph_writer_rules`** (research_runner): anti-fabrication rõ, check ngày xuất bản, ưu tiên nguồn chính/uy tín, yêu cầu provenance (URL thật) trên mọi Source/Evidence, nhận biết nguồn copy nhau.

## Non-goals (defer)
- **URL HEAD/GET verification** (best-effort, network-dependent, risky blind) — defer.
- **Render markdown link click được trong canvas/note** — đó là rendering, thuộc D (canvas) / E (note media).
- **Tự kiểm chứng kết luận** (cross-source fact-check pass) — lớn, defer.
- **Ranking nguồn uy tín tự động** — prompt-level only.

## Design
- New pure module `backend/topix/integrations/research_citation.py`:
  - `extract_urls(text) -> set[str]` — regex http(s) + markdown `[..](url)`.
  - `build_existing_url_index(nodes) -> dict[str, str]` — url → node_id (chỉ source/evidence).
  - `plan_dedup(node_inputs, existing_index) -> (to_create, reuse_map)` — pure, testable.
- Wire vào `integration.py` `batch_create`: build index từ `graph_store.get_graph(board_id)`, áp `plan_dedup`, reuse id cho edges.
- `research_runner.graph_writer_rules`: thêm rule 15 (citation integrity) — không bịa URL, check năm, ưu tiên primary, đánh dấu nguồn copy.

## Testing
- Unit `test_research_citation.py`: extract_urls, build_index, plan_dedup (reuse + passthrough + không dedupe non-source).
- Prompt rule: snapshot assertion trong test hiện có hoặc mới (rule text chứa keyword).
- Integration batch_create: defer (env thiếu dopplersdk; logic pure đã test).

## Out of scope
- URL verification, click-through rendering, auto fact-check, credibility ranking engine.