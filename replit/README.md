# Deploy Dim0 on Replit (stable public URL)

Single-origin deploy: the FastAPI backend serves the built webui (launcher +
canvas) at the Replit public URL. Postgres / Qdrant / Redis run on **external
managed free tiers** — Replit only runs the app.

## What you get

- `https://<repl>.replit.app/launcher.html` → research launcher
- `https://<repl>.replit.app` → dim0 canvas (boards + chat)
- Stable URL, always-on (Reserved VM), WebSocket + SSE work (no cloudflare-style drops).

## Prerequisites (free accounts, ~5 min)

Create these three and keep their credentials:

1. **Neon** (Postgres) — https://neon.tech → New project → copy connection string.
2. **Qdrant Cloud** — https://cloud.qdrant.io → Create cluster (free 1GB) → copy host + API key.
3. **Upstash** (Redis) — https://upstash.com → Create database → copy endpoint + password.
4. **Tavily** (optional, for web evidence) — https://tavily.com → API key.
5. Your **Ollama cloud** API key (already used locally) + the integration token + a JWT secret.

## Steps on Replit

1. **Import the repo**: Replit → Create Repl → Import from GitHub → paste
   `https://github.com/thangngny/dim0` (your fork) → language auto-detected as Python.
2. **Set Secrets**: Replit → Tools → Secrets → add every key from
   [`env.example`](./env.example) with your real values from the prerequisites.
3. **Run once**: click ▶ Run. `replit/start.sh` will:
   - install Python deps (`uv sync`),
   - install the Claude CLI (research agent),
   - build the webui (`npm run build`, same-origin config),
   - start the backend on `$PORT` (auto-applies the DB schema on first boot).
   Watch the console: "Uvicorn running on http://0.0.0.0:PORT" + no DB errors.
4. **Open the app**: the webview shows the launcher → signup a user → run research.
5. **Deploy (stable URL)**: Deployments → **Reserved VM** (NOT Autoscale — WS/SSE
   need always-on) → Deploy. You get `https://<repl>.replit.app`.

## Share

Give others:
- `https://<repl>.replit.app/launcher.html` (launcher)
- `https://<repl>.replit.app` (canvas)
- An account (they can also self-signup).

## Notes / gotchas

- **Reserved VM is paid** (~$15-25/mo). Autoscale (free) scale-to-zero drops the
  collab WebSocket + long research SSE → use Reserved VM for a stable share.
- **Ollama credits**: anyone with the URL can sign up + run research → burns
  your Ollama key. Dim0 has auth (signup/login) but no per-user quota yet.
- **Mini-app nodes** (sandboxed iframe runtime) are disabled in this deploy
  (`miniAppOrigin=""`). Research + canvas work normally; only the mini-app
  node type's preview is off.
- **Schema** auto-applies on every startup (idempotent, `CREATE ... IF NOT
  EXISTS`), so no manual migration step.
- **claude CLI** is installed by `start.sh` via the official installer; its
  model/provider come from the `ANTHROPIC_*` Secrets (Ollama cloud).
- If the webui build fails on Replit (Node version mismatch), run
  `cd webui && npm ci && npm run build` in the shell to see the error — the
  repo builds clean on Node 20+.