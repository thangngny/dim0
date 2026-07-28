# Agent transparency & control — live heartbeat, stop, error, pre-run confirm

- **Date:** 2026-07-27
- **Sub-project:** B (trong 9 sub-project của UX review)
- **Pain giải quyết:** mục 5 ("KO THỂ BIẾT NÓ ĐANG LÀM HAY KO", "Ko hiện tiến độ", "không giải thích nguyên nhân", "không dừng/làm lại riêng"), mục 13 ("Ko hiện tiến độ").
- **Scope đã chốt (recommended):** 4 feature feasible; defer 2 feature kiến trúc (plan-approval trước run + retry từng bước — Claude CLI subprocess one-shot không lộ step).

## 1. Mục tiêu

Xóa cảm giác "agent treo / không biết đang làm gì": khi Claude CLI đang trong giai đoạn im lặng dài (không stdout, không agent event), UI vẫn cho thấy **vẫn đang chạy + đã bao lâu + event gần nhất bao lâu trước**. Thêm nút Dừng, lỗi hiện rõ, và confirm trước khi refine mode ghi delta lên board.

## 2. Non-goals

- **Plan approval trước run** (duyệt kế hoạch từng bước trước khi agent làm): architecturally hard — Claude CLI subprocess one-shot không lộ step boundaries. Defer sang sub-project riêng nếu đổi sang interactive agent loop.
- **Retry riêng 1 bước / per-step control**: cùng hạn chế kiến trúc. Defer.
- Đổi `research_runner` sang multi-turn/interactive. Không đụng.
- Đổi webui board view (chỉ sửa launcher flow nơi user phản hồi).

## 3. Root cause (đã investigate)

- `research_runner.stream_research_claude` spawn `claude --output-format text`: stdout gần như chỉ emit kết quả cuối → giai đoạn "nghỉ" dài không có `status:progress`.
- Agent events tới từ `dim0_emit_research_event` (Claude tự gọi, thưa) → gap im lặng.
- Node-count (`BOARD_NODES=N`) chỉ đổi lúc ghi.
- UI đã wire đầy đủ (`readSSE` xử lý progress/agent/snapshot/error; webui `ResearchLivePanel` cũng vậy) — nhưng **không có heartbeat** giữa các event → trông đông cứng.

## 4. Architecture

Toàn bộ thay đổi ở **frontend launcher** (`webui/public/launcher.html`) + nhỏ ở **runner** (đã có sẵn, chỉ dùng lại). Không thêm endpoint.

```
readSSE start  → startHeartbeat(panel) : mỗi 1s cập nhật "đang chạy · Xs · event cuối Ys trước"
SSE event tới  → reset lastEventAt = now
SSE done/error → stopHeartbeat
Nút "Dừng"     → AbortController.abort() → fetch reject → stopHeartbeat + status "đã dừng"
status:error   → ngoài status bar, đẩy detail vào log box
refine "Chạy"  → mở confirm modal (mode+instruction+focus+ghi delta) → OK mới fetch
```

## 5. Changes — `webui/public/launcher.html`

### 5.1 Heartbeat trong `createAgentLiveUI`
- Thêm state: `startedAt`, `lastEventAt`.
- `ensureVisible()`/`applyEvent`/`applySnapshot`/`reset` set `startedAt=Date.now()`, `lastEventAt=Date.now()` khi có event/snapshot; `reset()` clear.
- Thêm `startHeartbeat()` / `stopHeartbeat()`: interval 1000ms cập nhật `metaId` text:
  - khi có agent running: `"running · Xs · event cuối Ys trước"` (X = elapsed từ startedAt, Y = now - lastEventAt).
  - Y > 20s: thêm dấu hiệu "🤔 đang suy nghĩ…".
- `metaId` hiện tại chỉ hiển thị khi có event — heartbeat làm nó hiện ngay khi run bắt đầu (kể cả khi 0 event).
- Heartbeat dừng ở `markAllDone()` / error / abort.

### 5.2 Nút "Dừng" trong explore + refine flow
- `doExplore`/`doRefine` (caller của `readSSE`) tạo `const ctrl = new AbortController()`, truyền `signal: ctrl.signal` vào `fetch`.
- Thêm nút "⏹ Dừng" cạnh status bar (explore: `clarifyStatus` region; refine: `refineStatus` region). Click → `ctrl.abort()` + `setStatus(..., "error"/"done", "đã dừng")` + `agentsUI.markAllDone()`.
- Abort → `fetch` reject → catch → dừng heartbeat + thông báo "Đã dừng (agent phía server có thể vẫn đang chạy — node đã ghi vẫn còn trên board)". Honest: không claim kill server proc.

### 5.3 Lỗi hiện trong log box
- Trong `readSSE` nhánh `ev.status === "error"`: ngoài `setStatus(statusId, "error", ...)`, gọi `logTo(logBoxId, "❌ " + (ev.detail || "Lỗi"), true)` để user thấy reason (stderr[:500]) trong log, không chỉ status bar một dòng.

### 5.4 Pre-run confirm cho refine (reframe/expand/critique)
- `doRefine` hiện chạy fetch ngay. Thêm confirm modal nhẹ (reuse pattern `confirm()` hoặc 1 div overlay):
  - Nội dung: `"Sắp chạy ${mode} — sẽ GHI DELTA lên board hiện tại.\nInstruction: ${instruction.slice(0,200)}\nFocus: ${focus_node_ids.length} node"`.
  - Nút "Chạy" → tiếp tục fetch; "Hủy" → return.
- Explore flow đã có clarify→scope (confirm ngầm) → không thêm.
- Dùng `window.confirm()` cho v1 (đơn giản, không cần CSS overlay). Sau này nâng lên modal nếu muốn.

## 6. Backend

Không đổi. `research_runner` đã emit đủ: `status:running/starting`, `status:progress` (stdout + BOARD_NODES), `status:agent`, `agents_snapshot`, `status:done` (early + nodes), `status:error` (detail). Heartbeat chỉ cần client-side timer.

## 7. Testing

Launcher = vanilla JS, không harness → **manual e2e** (Task list). Backend không đổi → không thêm test.

**Manual e2e:**
1. Explore vague brief → panel hiện ngay "running · 1s ·" (kể cả khi chưa có event), elapsed tăng mỗi giây, "🤔 đang suy nghĩ…" sau 20s im.
2. Refine (reframe) → modal confirm hiện → Hủy không chạy; Chạy mới fetch.
3. Giữa run → "⏹ Dừng" → status "đã dừng", heartbeat dừng, log ghi thông báo.
4. Ép lỗi (stop backend giữa run) → status "❌" + log box ghi stderr/reason.
5. Run hoàn tất → heartbeat dừng, meta "hoàn tất".

## 8. Risks

- **AbortController không kill server-side Claude**: honest message (agent có thể vẫn chạy server-side). Node đã ghi vẫn còn. Acceptable — không claim false.
- **Heartbeat timer leak**: ensure `stopHeartbeat` luôn gọi ở done/error/abort/catch + cleanup path.
- **`window.confirm()` block UI**: chấp nhận v1; refine run không phải thao tác high-frequency.

## 9. Out of scope

- Plan approval trước run + retry từng bước (architectural — interactive agent loop).
- Modal confirm đẹp (dùng `window.confirm` v1).
- webui board view changes (chỉ launcher).