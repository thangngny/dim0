# Agent transparency & control Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement. Steps use checkbox (`- [ ]`). Launcher is vanilla JS with no test harness → verification is manual e2e.

**Goal:** Kill the "agent treo / không biết đang làm gì" feeling: heartbeat+elapsed+last-event-ago in the live panel, a Stop button, error reason in the log, and a pre-run confirm for refine modes. No backend change.

**Architecture:** All changes in `webui/public/launcher.html`. Reuse existing `createAgentLiveUI`, `readSSE`, `logTo`, `setStatus`. Add a heartbeat timer to the agent UI, an `AbortController` per run wired to a Stop button, an error log line, and a `window.confirm()` gate in `doRefine`.

**Tech Stack:** vanilla JS (launcher.html), Fetch + ReadableStream SSE, AbortController.

## Global Constraints
- No backend changes. No new endpoints.
- Vanilla JS, no modules, no build — edits inside the existing `<script>` block and HTML.
- Honest messaging: abort stops the UI but does NOT kill the server-side Claude subprocess — say so.
- Commit format `type(scope): message`, scope `launcher`.
- Stop-heartbeat must always run on done/error/abort/catch to avoid timer leaks.

---

## Task 1: Heartbeat in `createAgentLiveUI`

**Files:** Modify `webui/public/launcher.html` (`createAgentLiveUI` ~L1140-1290)

- [ ] **Step 1:** In `createAgentLiveUI`, add timer state + helpers. After `const seen = new Set()` add:
```js
    let startedAt = 0
    let lastEventAt = 0
    let beatTimer = null

    function fmtAgo(ms) {
      const s = Math.max(0, Math.round(ms / 1000))
      if (s < 60) return `${s}s`
      return `${Math.floor(s / 60)}m${String(s % 60).padStart(2, "0")}s`
    }

    function startHeartbeat() {
      startedAt = Date.now()
      lastEventAt = Date.now()
      stopHeartbeat()
      const meta = document.getElementById(metaId)
      beatTimer = setInterval(() => {
        if (!meta) return
        const elapsed = Date.now() - startedAt
        const since = Date.now() - lastEventAt
        const thinking = since > 20000 ? " · 🤔 đang suy nghĩ…" : ""
        meta.textContent = `running · ${fmtAgo(elapsed)} · event cuối ${fmtAgo(since)} trước${thinking}`
      }, 1000)
    }

    function stopHeartbeat() {
      if (beatTimer) { clearInterval(beatTimer); beatTimer = null }
    }
```
- [ ] **Step 2:** In `applyEvent`, after `seen.add(id)` set `lastEventAt = Date.now()`. In `applySnapshot`, set `lastEventAt = Date.now()`. In `reset`, call `stopHeartbeat()` and reset `startedAt = lastEventAt = 0`.
- [ ] **Step 3:** In `markAllDone`, call `stopHeartbeat()` (before/after setting meta text "hoàn tất").
- [ ] **Step 4:** Expose `startHeartbeat`/`stopHeartbeat` in the returned object: `return { reset, applyEvent, applySnapshot, markAllDone, ensureVisible, startHeartbeat, stopHeartbeat }`.
- [ ] **Step 5 (manual):** Start an explore run; panel `meta` shows `running · 1s · event cuối 1s trước` immediately and ticks each second; after 20s of silence shows `🤔 đang suy nghĩ…`; on done shows `hoàn tất` and stops ticking.

## Task 2: Start/stop heartbeat in `readSSE` + error to log

**Files:** Modify `webui/public/launcher.html` (`readSSE` ~L1306)

- [ ] **Step 1:** In `readSSE`, after `if (agentsUI) agentsUI.reset()` add `if (agentsUI) agentsUI.startHeartbeat()`.
- [ ] **Step 2:** In the `ev.status === "done"` branch, keep `agentsUI.markAllDone()` (it stops heartbeat). In the `ev.status === "error"` branch add `logTo(logBoxId, "❌ " + (ev.detail || "Lỗi"), true)` before `onError`.
- [ ] **Step 3:** Wrap the whole `while(true)` read loop so that if the reader throws (abort/network), the catch stops heartbeat. Add a `try { ... } finally { if (agentsUI) agentsUI.stopHeartbeat() }` around the loop body, OR stop heartbeat in the outer caller's catch (see Task 3). Simplest: add `finally { if (agentsUI) agentsUI.stopHeartbeat() }` to `readSSE` after the loop.
- [ ] **Step 4 (manual):** Stop backend mid-run → status `❌` AND log box shows the reason; heartbeat stops.

## Task 3: Stop button + AbortController

**Files:** Modify `webui/public/launcher.html` (HTML near `clarifyStatus`/`refineStatus`; `doExplore` caller ~L1430; `doRefine` ~L1485; `readSSE` signature)

- [ ] **Step 1:** Add a Stop button to the DOM. Near the explore status (`#generateStatus`) and refine status (`#refineStatus`) regions, add:
```html
<button type="button" class="btn secondary" id="stopExploreBtn" style="display:none">⏹ Dừng</button>
<button type="button" class="btn secondary" id="stopRefineBtn" style="display:none">⏹ Dừng</button>
```
(Place next to the existing buttons; exact location flexible.)
- [ ] **Step 2:** Add a module-level `let currentAbort = null` near other `let` state (e.g. near `let boardId = ""`).
- [ ] **Step 3:** In `doExplore`'s fetch and `doRefine`'s fetch, build `const ctrl = new AbortController(); currentAbort = ctrl;` and pass `signal: ctrl.signal` to the `fetch(...)` options.
- [ ] **Step 4:** Wire the Stop buttons:
```js
function bindStop(stopBtnId, agentsUI, statusId, logBoxId) {
  const b = document.getElementById(stopBtnId)
  if (!b) return
  b.addEventListener("click", () => {
    if (currentAbort) { try { currentAbort.abort() } catch {} currentAbort = null }
    if (agentsUI) agentsUI.stopHeartbeat()
    setStatus(statusId, "error", "⏹ Đã dừng — agent phía server có thể vẫn đang chạy; node đã ghi vẫn còn trên board.")
    logTo(logBoxId, "⏹ Đã dừng bởi user (agent server có thể vẫn chạy).", true)
    b.style.display = "none"
  })
}
bindStop("stopExploreBtn", exploreAgentsUI, "generateStatus", "logBox")
bindStop("stopRefineBtn", refineAgentsUI, "refineStatus", "refineLogBox")
```
- [ ] **Step 5:** Show/hide Stop buttons: show when a run starts (`agentsUI.startHeartbeat()` area or right before fetch), hide on done/error/catch. Minimal: set `stopExploreBtn.style.display="inline-block"` in `doExplore` before fetch and `"none"` in its `finally`; same for refine.
- [ ] **Step 6:** In the outer `catch` of `doExplore`/`doRefine`, if `e.name === "AbortError"` suppress the generic error status (the Stop handler already set it); otherwise show `e.message`.
- [ ] **Step 7 (manual):** Mid-run click Stop → status shows "⏹ Đã dừng…", log notes it, panel stops ticking, board keeps nodes already written.

## Task 4: Pre-run confirm for refine

**Files:** Modify `webui/public/launcher.html` (`doRefine` ~L1485)

- [ ] **Step 1:** In `doRefine`, after computing `mode`, `instruction`, `focus_node_ids` (before disabling the button / fetching), add:
```js
    const focusN = focus_node_ids.length
    const ok = window.confirm(
      `Chạy ${mode.toUpperCase()} — sẽ GHI ĐÈN (delta) lên board hiện tại.\n\n` +
      `Instruction: ${instruction.slice(0, 200)}${instruction.length > 200 ? "…" : ""}\n` +
      `Focus: ${focusN ? focusN + " node" : "(toàn board)"}\n\n` +
      `Nhấn OK để chạy, Cancel để hủy.`
    )
    if (!ok) return
```
- [ ] **Step 2 (manual):** Refine (reframe) → confirm dialog shows mode+instruction+focus; Cancel aborts (no fetch); OK runs.

## Task 5: Final manual e2e checklist
- [ ] Explore: panel meta ticks from 1s, "🤔" after 20s silent, stops at done.
- [ ] Refine: confirm dialog before run; Cancel = no run.
- [ ] Stop button mid-run (explore + refine): honest "đã dừng" message, heartbeat stops.
- [ ] Forced error: reason appears in both status bar and log box.
- [ ] No heartbeat timer leak (run → done → run again works; DevTools no orphan intervals).

## Self-Review
- Spec coverage: heartbeat §5.1 → T1; start/stop in readSSE + error §5.3,§5.2 → T2; stop button §5.2 → T3; pre-confirm §5.4 → T4; e2e §7 → T5. ✓
- No placeholders. Type/name consistency: `startHeartbeat`/`stopHeartbeat` used in T1, T2, T3 consistently. `currentAbort` consistent. `bindStop` consistent.
- Backend untouched (per spec §6). ✓