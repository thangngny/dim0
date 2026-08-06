#!/usr/bin/env bash
# AWS EC2 bootstrap for dim0 (single-origin deploy).
#
# Idempotent-ish: safe to re-run. Installs system deps, Python 3.13 (via uv),
# Node 22, the Claude CLI (research agent), builds the webui same-origin, and
# installs the systemd unit + nginx site. The backend itself is started by
# systemd (dim0-backend.service), NOT by this script.
#
# Run on a fresh Ubuntu 24.04 LTS EC2 instance (t3.medium, 4GB RAM) as the
# `ubuntu` user with sudo. See README.md for the full walkthrough.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

# Default install location; override with DIM0_INSTALL_DIR=/opt/dim0.
INSTALL_DIR="${DIM0_INSTALL_DIR:-/opt/dim0}"
SERVICE_USER="${DIM0_SERVICE_USER:-ubuntu}"
BACKEND_PORT="${DIM0_BACKEND_PORT:-8080}"

echo "==> [1/8] System packages (nginx, build tools, Node 22)"
sudo apt-get update -qq
sudo apt-get install -y -qq curl ca-certificates build-essential git nginx \
  >/dev/null
if ! command -v node >/dev/null 2>&1 || ! node -v | grep -qE 'v(2[02])'; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - >/dev/null
  sudo apt-get install -y -qq nodejs >/dev/null
fi
echo "    node $(node -v), npm $(npm -v)"

echo "==> [2/8] uv (Python package manager)"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
fi
export PATH="$HOME/.local/bin:$PATH"
echo "    uv $(uv --version)"

echo "==> [3/8] Backend Python 3.13 + deps (uv managed, no system python change)"
cd "$ROOT/backend"
uv python install 3.13 >/dev/null
uv sync --quiet
PY="backend/.venv/bin/python"
cd "$ROOT"
echo "    $($PY --version)"

echo "==> [4/8] Claude CLI (research agent subprocess)"
if ! command -v claude >/dev/null 2>&1; then
  curl -fsSL https://claude.ai/install.sh | bash \
    || echo "  (claude install failed — research will be unavailable; check network)"
fi

echo "==> [5/8] Webui same-origin config (apiBase/BACKEND empty → relative)"
mkdir -p webui/public
cat > webui/public/config.js <<'CFG'
window.__APP_CONFIG__ = {
  apiBase: "",
  billingEnabled: "false",
  miniAppOrigin: "",
  hostOrigin: "",
}
CFG
python3 - <<'PY'
import re, pathlib
p = pathlib.Path("webui/public/launcher.html")
t = p.read_text()
t = re.sub(r'const BACKEND\s*=\s*"[^"]*"', 'const BACKEND  = ""', t)
t = re.sub(r'const FRONTEND\s*=\s*"[^"]*"', 'const FRONTEND = ""', t)
p.write_text(t)
PY
# The webui build reads ../.env (dotenv). Provide a minimal same-origin one
# if the operator hasn't supplied real values.
[ -f .env ] || cat > .env <<'ENV'
VITE_API_URL=
VITE_HOST_ORIGIN=
VITE_MINI_APP_ORIGIN=
API_ORIGIN=
DIM0_BASE_URL=
APP_BASE_URL=
ENV

echo "==> [6/8] Webui build (tsc -b && vite build) — may take ~30-60s"
export NODE_OPTIONS="--max-old-space-size=4096"
(cd webui && npm ci --silent && npm run build)
echo "    dist: $(find webui/dist -mindepth 1 -maxdepth 1 | wc -l) entries"

echo "==> [7/8] Install app to $INSTALL_DIR"
sudo mkdir -p "$INSTALL_DIR"
# rsync if available (excludes .git/.venv/node_modules), else cp.
if command -v rsync >/dev/null 2>&1; then
  sudo rsync -a --delete \
    --exclude '.git' --exclude 'backend/.venv' --exclude 'webui/node_modules' \
    "$ROOT/" "$INSTALL_DIR/"
else
  sudo cp -a "$ROOT"/* "$INSTALL_DIR/"
fi
sudo chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR"

echo "==> [8/8] systemd unit + nginx site"
sudo mkdir -p /etc/dim0
if [ ! -f /etc/dim0/env ]; then
  sudo cp "$ROOT/deploy/aws/env.example" /etc/dim0/env
  sudo chmod 600 /etc/dim0/env
  sudo chown root:"$SERVICE_USER" /etc/dim0/env
  echo "    Created /etc/dim0/env from template — EDIT IT with real DB creds now."
fi
# Patch DIM0_WEBUI_DIST + PORT in the env file to match this install.
sudo sed -i \
  -e "s|^DIM0_WEBUI_DIST=.*|DIM0_WEBUI_DIST=$INSTALL_DIR/webui/dist|" \
  -e "s|^PORT=.*|PORT=$BACKEND_PORT|" \
  /etc/dim0/env

# systemd unit — substitute placeholders from the committed template.
sudo sed \
  -e "s|__INSTALL_DIR__|$INSTALL_DIR|g" \
  -e "s|__SERVICE_USER__|$SERVICE_USER|g" \
  -e "s|__BACKEND_PORT__|$BACKEND_PORT|g" \
  "$ROOT/deploy/aws/dim0-backend.service" \
  | sudo tee /etc/systemd/system/dim0-backend.service >/dev/null
sudo touch /var/log/dim0-backend.log
sudo chown "$SERVICE_USER":"$SERVICE_USER" /var/log/dim0-backend.log

# nginx site — substitute the backend port from the committed template.
sudo sed "s|__BACKEND_PORT__|$BACKEND_PORT|g" "$ROOT/deploy/aws/nginx/dim0.conf" \
  | sudo tee /etc/nginx/sites-available/dim0 >/dev/null
sudo ln -sf /etc/nginx/sites-available/dim0 /etc/nginx/sites-enabled/dim0
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t

sudo systemctl daemon-reload
sudo systemctl enable --now nginx

echo
echo "==> Done. Next steps:"
echo "    1. Edit /etc/dim0/env with your real Neon/Qdrant/Upstash + Ollama creds."
echo "       (DIM0_INTEGRATION_TOKEN must match the token in launcher.html)"
echo "    2. sudo systemctl enable --now dim0-backend"
echo "    3. curl http://localhost/integration/health   # should be {\"status\":\"ok\"}"
echo "    4. Open http://<EC2-PUBLIC-IP>/launcher.html"