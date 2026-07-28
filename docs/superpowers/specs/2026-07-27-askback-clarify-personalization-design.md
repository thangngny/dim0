# Ask-back cá nhân hóa — Clarify pass dùng Claude CLI

- **Date:** 2026-07-27
- **Sub-project:** A (trong 9 sub-project của UX review)
- **Pain giải quyết:** mục 1, 4, 5, 6, 10 — "bộ câu hỏi generic, không cá nhân hóa; prompt mờ thì agent tự suy đoán thay vì hỏi lại; phải brief siêu kĩ".
- **Approach đã chốt:** A — Clarify pass riêng + answers fold vào research prompt.

## 1. Mục tiêu

Trước khi chạy research, **chính agent (Claude CLI) đọc board + instruction, tự đánh giá độ rõ**, và chỉ hỏi lại **đúng gap** (0–4 câu, gắn `axis`), thay vì bộ câu hỏi generic hiện tại (Ollama template). Khi brief đã rõ → 0 câu → chạy thẳng. Giống hành vi "Claude chat hỏi đào sâu theo từng prompt".

Giá trị: giảm friction "phải brief siêu kĩ", cá nhân hóa theo board state + mode + instruction cụ thể.

## 2. Non-goals

- Ask-back GIỮA research (Approach B pause/resume SSE) — sub-project riêng, scope (a) đã chốt một lần trước research.
- Đổi runner subprocess model, multi-round clarify, bỏ hẳn Ollama (giữ làm fallback).
- Đụng tới 8 sub-project còn lại (layout, source, note media, multi-board, v.v.).

## 3. Architecture

Hai lần Claude CLI, tách rời, launcher orchestrate tuần tự (fit đúng UI 3-phase sẵn có):

```
Launcher doClarifyQuestions()
  → POST /integration/research/clarify
      { topic, stage:"questions", board_id, mode, focus_node_ids, language }
     → run_clarify → spawn Claude CLI clarify pass
        (đọc board qua MCP: dim0_get_board / dim0_list_nodes, focus subtree)
        → JSON { clear, questions:[{id,question,why,hint,options,axis}], rationale }
     → fallback Ollama _complete_json → _fallback_questions (static)
  ← { clear, questions, rationale, model }

  if clear==true  → skip questions, chạy Explore (instruction = topic)
  else            → renderQuestions (nhóm theo axis), user trả lời
     → POST /integration/research/clarify { stage:"scope", answers, axes }
        → fold deterministic → scope_brief (không LLM)
     ← { scope_brief, research_axes, ... }

  → POST /integration/boards/{id}/research
      { instruction: scope_brief, mode, focus_node_ids, language, ... }
     → research_runner (Claude CLI research pass) — KHÔNG đổi
```

Contract giữ nguyên: launcher vẫn truyền `scope_brief` làm `instruction` cho research runner → **runner zero change**.

## 4. Backend changes

### 4.1 `backend/topix/integrations/research_clarify.py`

**Schema additions:**
- `ClarifyRequest`: thêm `board_id: str | None`, `mode: str | None` (explore|reframe|expand|critique), `focus_node_ids: list[str] = []`.
- `ClarifyQuestion`: thêm `axis: str` (storyline|tone|craft|industry|scope|other), default `"other"`.
- `ClarifyQuestionsOut`: thêm `clear: bool = False`, `rationale: str = ""`.

**New function:** `build_clarify_prompt(*, board_id, mode, instruction, focus_node_ids, language) -> str`
- Prompt nhỏ, `--effort high` (không ultracode — clarify cần nhanh, không cần orchestrate sub-agent).
- Nội dung:
  - Role: "You are the Dim0 research lead about to run MODE=<mode>."
  - Step 1: "Read the board via dim0_get_board / dim0_list_nodes (board_id pinned)."
  - Step 2: "Assess whether INSTRUCTION + current board state is clear enough to start MODE without guessing."
  - Output rules: `clear:true` + `rationale` nếu rõ; ngược lại `clear:false` + `questions[]` (1–4 câu, mỗi câu 1 gap cụ thể, gắn `axis`, có `why`/`hint`/tuỳ chọn `options` ≤4). Không hỏi cái đã trả lời được từ board/instruction. Vi if language starts "vi".
  - Output ONLY JSON, không prose, không markdown fence.
  - Meta block: `BOARD_ID`, `MODE`, `FOCUS_NODE_IDS`, `INSTRUCTION`.
- Explore (board trống): get_board trả empty → câu hỏi thuần từ instruction.
- Reframe/expand/critique: instruct agent xét focus subtree + gap của mode đó (vd expand: "đào sâu cụm này còn thiếu gì?").

**New function:** `run_clarify_questions_claude(body, *, board_id, mode, focus_node_ids) -> ClarifyQuestionsOut`
- Resolve `claude` bin (`shutil.which("claude")`); env pin `DIM0_DEFAULT_BOARD_ID=board_id`, `DIM0_BASE_URL`, `DIM0_INTEGRATION_TOKEN` (giống research_runner).
- Spawn `claude --dangerously-skip-permissions --effort high -p <prompt> --output-format text`, cwd=repo_root, timeout 90s.
- Parse stdout via `_extract_json_object` (sẵn có).
- Map → `ClarifyQuestionsOut` (clear, questions với axis, rationale, model="claude-cli").
- Parse fail / timeout / no bin → raise (caller fallback).

**Rewrite `run_clarify` (stage=questions):**
- Try `run_clarify_questions_claude` first.
- Exception → log + fallback tới Ollama `_complete_json` (sẵn có) → fallback `_fallback_questions`.
- Mỗi câu hỏi fallback thêm `axis` (gán theo content heuristic hoặc `"other"`).

**Rewrite `run_clarify` (stage=scope):**
- **Deterministic fold** (bỏ LLM scope): build `scope_brief` từ `topic` + `answers` + `axes` (lấy axis từ questions hoặc default 3 trục). Dùng lại logic `_fallback_scope` (sẵn có, chất lượng OK).
- Giữ `ClarifyScopeOut` shape (problem_statement, goals, in_scope, out_scope, research_axes, success_criteria, open_assumptions, industry_traits, scope_brief, model="fold").
- (Tùy chọn sau: flag `smart_scope=true` gọi LLM scope — mặc định off.)

### 4.2 `backend/topix/api/router/integration.py`

Endpoint `/integration/research/clarify` hiện nhận `ClarifyRequest` (topic, language, stage, answers) — không có board context.
- Truyền thêm `board_id`/`mode`/`focus_node_ids`: vì `ClarifyRequest` giờ có các field này, endpoint chỉ cần forward. **Lưu ý:** endpoint hiện là `Depends(_verify_token)` (integration token) — launcher đã gửi token, OK.
- Không thêm endpoint mới.

## 5. Launcher UI changes — `webui/public/launcher.html`

### 5.1 `doClarifyQuestions()`
- Payload thêm `board_id` (từ currentBoardId), `mode` (mặc định `"explore"`, hoặc theo step hiện tại), `focus_node_ids` (rỗng cho explore, focus node cho expand).
- Response: `d.clear`, `d.questions`, `d.rationale`, `d.model`.
- `clear==true`:
  - setStatus done: `✅ Agent thấy đề bài đã rõ — chạy Explore thẳng`.
  - Hiển thị `rationale` ngắn trong `clarifyStatus`.
  - Tự nhảy scope/explore (gọi `skipClarifyExplore()` flow nhưng giữ rationale cho user thấy) — hoặc render phase "đã rõ" với 1 nút "Chạy Explore". Chốt: render phase questions rỗng + 1 banner rationale + auto‑trigger explore sau 1s (cho user kịp đọc) — hoặc đơn giản hơn: nút "Chạy Explore" để user chủ động. **Chốt: nút chủ động** (tránh chạy lệnh tốn tiền khi user chưa sẵn sàng).
- `clear==false`: `renderQuestions(questions)` rồi `showClarifyPhase("questions")`.

### 5.2 `renderQuestions(questions)`
- **Nhóm theo `q.axis`**: render section header trước mỗi group (Storyline / Tone & Mood / Thủ pháp / Ngành / Phạm vi / Khác). Map `axis` → label VN + icon.
- Mỗi card: thêm axis chip nhỏ cạnh số thứ tự; giữ `question`/`why`/`hint`/`options` hiện tại.
- Nếu 0 questions nhưng `clear==false` (edge): show "Không có câu hỏi cụ thể — agent sẽ tự xử lý" + nút chạy.

### 5.3 Giữ nguyên
- `skipClarifyExplore()` (Bỏ qua clarify → Explore thẳng) — vẫn cần cho user muốn skip.
- `doClarifyScope()` — giờ trả deterministic nhanh; UI giữ.
- 3 phase `clarifyTopicPhase`/`clarifyQuestionsPhase`/`clarifyScopePhase`.

### 5.4 Override "Vẫn muốn hỏi thêm" (tùy chọn v1, có thể bỏ)
- Khi `clear==true`, thêm nút nhỏ "Vẫn muốn được hỏi" → force fallback questions. **Quyết định v1: BỎ** — giữ đơn giản; `clear` + "Bỏ qua clarify" đủ.

## 6. Error handling & fallback

| Trường hợp | Hành vi |
|---|---|
| `claude` bin not found | fallback Ollama `_complete_json` → `_fallback_questions` (static) |
| Claude CLI exit non-zero | log + fallback Ollama → static |
| JSON parse fail (sau retry) | fallback Ollama → static |
| Timeout 90s | kill proc + fallback |
| `clear:true` + user muốn control | nút "Bỏ qua clarify → Explore" vẫn có |
| MCP read board fail trong clarify pass | agent vẫn output (board rỗng) — questions thuần từ instruction |

Fallback luôn đảm bảo trả được questions (không bao giờ block user).

## 7. Testing

**Unit (`backend/test/unit/`):**
- `_extract_json_object` với payload `{clear, questions}` (có/không fence).
- `build_clarify_prompt` snapshot (mode=explore/expand, vi/en).
- Deterministic scope fold: input answers → scope_brief ổn định.

**Integration (`backend/test/integration/`):**
- Stub `shutil.which("claude")` trả path giả + monkeypatch subprocess trả JSON clear/unclear → verify `run_clarify` trả đúng `clear`/`axis`.
- Stub CLI raise → verify fallback Ollama/static được gọi.
- Endpoint `/integration/research/clarify` với board_id+mode → forward đúng.

**Manual e2e:**
1. Board trống + instruction "research ref sáng tạo cho BHNT gia đình VN" → questions về ngành/insight/ref (không generic).
2. Board có content + mode=expand + focus node cụ thể → questions tham chiếu gap của cụm đó.
3. Instruction siêu rõ (có goals/KPI/scope) → `clear:true` + rationale.

## 8. Scope / interfaces / isolation

- **Unit `research_clarify.py`**: thêm `build_clarify_prompt` + `run_clarify_questions_claude`; sửa `run_clarify`. Interface ra: `ClarifyQuestionsOut` (thêm clear/rationale/axis). Caller duy nhất: integration router `/research/clarify`.
- **Unit `integration.py` router**: forward thêm field `ClarifyRequest`; không thêm endpoint.
- **Unit `launcher.html`**: 2 hàm `doClarifyQuestions`/`renderQuestions`; không động phase structure.
- **Runner (`research_runner.py`)**: KHÔNG đụng.
- Mỗi unit test độc lập; fallback chain đảm bảo không regression (Ollama path cũ vẫn chạy khi CLI fail).

## 9. Risks

- **Latency**: Claude CLI clarify pass ~10–30s (load MCP + read board). Chấp nhận được (Ollama hiện tại cũng mất thời gian). Nếu chậm quá → sau này thêm cache board snapshot.
- **Token cost**: 2 CLI call thay vì 1 (hiện chỉ research). Clarify pass nhỏ (`--effort high`, prompt ngắn) nên cost thấp. Expand scope: focus subtree nhỏ.
- **`clear` sai** (agent bảo rõ nhưng thực ra mờ): user có "Bỏ qua clarify" + thể force explore; không block. Đánh giá sau qua dùng thật.
- **MCP read board trong clarify** phụ thuộc integration API up + token đúng — env pin giống research_runner nên ổn định.

## 10. Out of scope (ghi cho rõ)

- Approach B (pause/resume SSE mid-research).
- Multi-round clarify (1–2 vòng).
- Thay runner subprocess model.
- 8 sub-project còn lại của UX review.