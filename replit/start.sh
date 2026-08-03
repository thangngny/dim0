#!/usr/bin/env bash
# Replit bootstrap: install deps, build the webui (same-origin), install the
# Claude CLI (research agent), then start the backend serving launcher + canvas
# at the Replit public URL. External Postgres/Qdrant/Redis come from Replit Secrets.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PATH="$HOME/.local/bin:$PATH"

echo "==> [1/5] Backend Python deps (uv)"
if command -v uv >/dev/null 2>&1; then
  (cd backend && uv sync --quiet)
  PY="backend/.venv/bin/python"
else
  (cd backend && pip install -e . --quiet)
  PY="python"
fi

echo "==> [2/5] Claude CLI (research agent subprocess)"
if ! command -v claude >/dev/null 2>&1; then
  # Native Claude Code binary. Auth + model come from ANTHROPIC_* Secrets.
  curl -fsSL https://claude.ai/install.sh | bash || echo "  (claude install failed — research will be unavailable; check network)"
fi

echo "==> [3/5] Webui build (same-origin: backend serves it)"
# config.js + launcher.html call the backend on the SAME origin (Replit URL),
# so apiBase/BACKEND are empty → requests are relative ("/boards/...").
mkdir -p webui/public
cat > webui/public/config.js <<'CFG'
window.__APP_CONFIG__ = {
  apiBase: "",
  billingEnabled: "false",
  miniAppOrigin: "",
  hostOrigin: "",
}
CFG
# launcher.html hardcodes BACKEND/FRONTEND — make them same-origin too.
python3 - <<'PY'
import re, pathlib
p = pathlib.Path("webui/public/launcher.html")
t = p.read_text()
t = re.sub(r'const BACKEND\s*=\s*"[^"]*"', 'const BACKEND  = ""', t)
t = re.sub(r'const FRONTEND\s*=\s*"[^"]*"', 'const FRONTEND = ""', t)
p.write_text(t)
PY
# The webui build reads ../.env (dotenv). Provide a minimal one from Secrets.
[ -f .env ] || cat > .env <<'ENV'
VITE_API_URL=
VITE_HOST_ORIGIN=
VITE_MINI_APP_ORIGIN=
API_ORIGIN=
DIM0_BASE_URL=
APP_BASE_URL=
ENV
(cd webui && npm ci --silent && npm run build)

echo "==> [4/5] Schema auto-applies on backend startup (idempotent) against POSTGRES_*"

echo "==> [5/5] Start backend serving webui dist on \${PORT:-8080}"
export DIM0_WEBUI_DIST="$ROOT/webui/dist"
export PORT="${PORT:-8080}"
cd "$ROOT/backend"
exec $PY -m topix.api.app --port "$PORT"