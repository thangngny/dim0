# Converter Heartbeat Streaming — Fix 504 on long-running converter endpoints

Date: 2026-08-07
Status: Approved (Option B)
Scope: `backend/topix/api/router/tools.py` + shared helper + nginx config

## Problem

The 7 converter endpoints under `/tools/*` run a synchronous LLM agent call
(`AgentRunner.run(...)`) that takes ~50s (variable, up to >100s) and then return
a single JSON body. Through the public path (browser → Cloudflare Worker →
nginx → uvicorn) this intermittently returns **504 Gateway Timeout**:

- Cloudflare's edge gives up when the origin sends no first byte within ~100s.
- nginx's default `location /` uses `proxy_read_timeout 60s` + buffering on.

E2E evidence (2026-08-07): `POST /tools/mindmaps:mapify` returned 504 via the
public proxy, while the same call replayed directly on the origin
(`localhost:8080`) returned 200 in 49.8s with a valid notes/links payload. The
backend is healthy; the failure is purely a proxy timeout on a long,
no-keepalive response.

All 7 converters share the identical synchronous pattern, so they all carry
the same 504 risk:
`notify`, `mapify`, `schemify`, `summify`, `quizify`, `drawify` (in `tools.py`)
and `translate` (in `tools.py`). The `:describe` endpoints are **not** affected
and are out of scope.

## Goal

Make every converter endpoint return 200 through the public proxy regardless of
agent duration, with **no frontend change** and a change localized to the
converter layer + nginx.

## Approach (Option B — heartbeat streaming)

While the agent runs, the endpoint emits a keep-alive byte (`\n`) every ~5s,
then emits the final JSON. Whitespace before the JSON is ignored by the
frontend's `JSON.parse`, so the existing `apiFetch` consumer is unchanged. The
first byte arrives within ~5s (well under Cloudflare's ~100s first-byte limit)
and inter-byte gaps are ~5s (well under nginx's 60s gap and Cloudflare's ~100s
gap), so neither proxy times out.

This mirrors the existing precedent in the codebase: the research endpoint
streams via `with_streaming_resilient_ndjson` with nginx `proxy_buffering off`
+ `proxy_read_timeout 3600s`. The Cloudflare Worker already passes streamed
bodies through (`return new Response(resp.body, …)`).

## Design

### Backend — shared helper

Add `stream_tool_conversion(...)` in a new module
`backend/topix/api/utils/streaming_tool.py` (keeps the converter streaming
helper separate from the existing `resilient_streaming.py` used by chat/research
NDJSON). It returns a `fastapi.responses.StreamingResponse`:

- `media_type = "application/json"` (so the frontend `apiFetch` uses `res.json()`).
- Header `X-Accel-Buffering: no` (instructs nginx not to buffer this response,
  belt-and-suspenders alongside the nginx config).
- Body async generator:
  1. `task = asyncio.create_task(run_fn(answer, context=context))` — the agent
     run wrapped so it executes concurrently with the heartbeat loop.
  2. Loop with `asyncio.wait({task}, timeout=5)`:
     - on timeout (task not done) → `yield "\n"`;
     - on task done → break.
  3. `result = await task`; compute `notes, links = convert_fn(result)`;
     `yield json.dumps({"status": "success", "data": {"notes": [...], "links": [...]}})`.
  4. On task exception → `yield json.dumps({"status": "error", "data": {"message": "..."}})`
     (matches the existing `@with_standard_response` error shape; status stays
     200 because headers were already flushed with the heartbeat).
  5. If the generator is closed early (client disconnect) → `task.cancel()` to
     avoid leaking the in-flight agent run.

Notes serialization uses the same `model_dump(exclude_none=True)` pattern the
endpoints use today. The `translate` endpoint returns a single note (no links),
handled by the same helper via its `convert_fn`.

### Backend — endpoint wiring

For each of the 7 converters, replace the `@with_standard_response` dict-return
body with `return stream_tool_conversion(...)`. Concretely each endpoint keeps
its auth (`get_current_user_uid`), `body`, and `rate_limiter` dependencies, and
constructs the agent + a thin `convert_fn`, then delegates to the helper. The
`response: Response` / `request: Request` params are removed where unused.

### nginx

Add a `location /tools` block mirroring `/integration/research/`:
`proxy_buffering off`, `proxy_cache off`, `proxy_read_timeout 3600s`,
`proxy_send_timeout 3600s`, plus the standard `proxy_set_header` set. Edit the
template in `deploy/aws/nginx/` and apply the same change to the installed
config on the instance, then `nginx -t` + reload.

### Worker

No change (already streams `resp.body`).

### Frontend

No change. `apiFetch` does `res.json()` on `application/json`, which ignores the
leading heartbeat whitespace. No client-side timeout is set on these calls.

## Why this defeats the 504

- First byte within ~5s → below Cloudflare's ~100s first-byte ceiling.
- Inter-byte gap ~5s → below nginx's 60s read gap and Cloudflare's ~100s gap.
- nginx buffering disabled (config + `X-Accel-Buffering: no`) → bytes are not
  held until the response completes.
- Worker passes the stream through unchanged.

## Error handling

- Agent raises → yield `{status:"error", data:{message}}` (200), preserving
  current `@with_standard_response` semantics (frontend receives a body it can
  parse; `convertToMindMap` surfaces the failure via its existing mutation
  error path).
- Client disconnects mid-stream → cancel the agent task; log a warning.
- Rate-limit / auth failures are unchanged (handled by deps before streaming
  starts, so no heartbeat is sent for a rejected request).

## Testing

1. **Local/origin**: `curl -N http://localhost:8080/tools/mindmaps:mapify`
   with a sample answer → observe newline heartbeats arriving before the final
   JSON; assert `http=200` and `data.notes` non-empty.
2. **Public proxy**: same `curl -N` against
   `https://dim0-proxy.dim0-thang.workers.dev/tools/mindmaps:mapify` → `http=200`
   (not 504), final JSON valid.
3. **Browser e2e**: replay the "convert answer to mind map" flow used during
   diagnosis → board populates with notes, no 504, board `:describe` then runs.
4. **Negative**: trigger an agent failure (e.g., malformed answer that the agent
   rejects) → response is 200 with `{status:"error"}`, no 5xx, no leaked
   exception text.
5. **Disconnect**: cancel the client mid-stream → backend logs the cancel
   warning, no leaked task (verify via no lingering process / next request
   still healthy).

## Deploy

1. Commit + push to fork (`thangngny/dim0`).
2. Instance: `git pull`, rsync changed `tools.py` + new helper to
   `/opt/dim0/backend/`, rsync nginx template, apply nginx config on instance,
   `nginx -t && systemctl reload nginx`, `systemctl restart dim0-backend`.
3. Re-run the public-proxy `curl -N` and browser e2e to confirm.

## Out of scope

- `:describe` endpoints (already resilient; unrelated).
- Changing the converter model or prompt (latency is inherent; the fix is
  transport-level).
- Async job + polling (Option A) — deferred; revisit only if heartbeat
  streaming proves insufficient (e.g., multi-minute runs or many concurrent
  conversions).