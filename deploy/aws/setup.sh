#!/usr/bin/env bash
# AWS EC2 bootstrap for dim0 (single-origin deploy).
#
# Idempotent-ish: safe to re-run. Installs system deps, Python 3.13 (via uv),
# Node 22, the Claude CLI (research agent), builds the webui same-origin, and
# installs the systemd unit + nginx site. The backend itself is started by
# systemd (dim0-backend.service), NOT by this script.
#
# Run on a fresh Ubuntu 24.04 LTS EC2 instance as the `ubuntu` user with sudo.
# See README.md for the full walkthrough.
#
# Set DIM0_SKIP_BUILD=1 on a small instance (t3.micro, 1GB RAM) to skip the
# in-place webui build — instead build dist locally and scp it to
# $INSTALL_DIR/webui/dist afterwards. Also skips Node install (backend only
# needs Python).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

# Default install location; override with DIM0_INSTALL_DIR=/opt/dim0.
INSTALL_DIR="${DIM0_INSTALL_DIR:-/opt/dim0}"
SERVICE_USER="${DIM0_SERVICE_USER:-ubuntu}"
BACKEND_PORT="${DIM0_BACKEND_PORT:-8080}"
SKIP_BUILD="${DIM0_SKIP_BUILD:-0}"

echo "==> [1/9] System packages (nginx, build tools)"
sudo apt-get update -qq
sudo apt-get install -y -qq curl ca-certificates build-essential git nginx \
  >/dev/null
if [ "$SKIP_BUILD" = "0" ]; then
  if ! command -v node >/dev/null 2>&1 || ! node -v | grep -qE 'v(2[02])'; then
    curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - >/dev/null
    sudo apt-get install -y -qq nodejs >/dev/null
  fi
  echo "    node $(node -v), npm $(npm -v)"
else
  echo "    DIM0_SKIP_BUILD=1 — skipping Node (build done locally, dist uploaded)"
fi

echo "==> [2/9] Swap (2GB) — small-instance OOM guard for runtime"
if ! sudo swapon --show | grep -q "/swapfile"; then
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile >/dev/null
  sudo swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
  echo "    swap +2G enabled"
else
  echo "    swap already present"
fi

echo "==> [3/9] uv (Python package manager)"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
fi
export PATH="$HOME/.local/bin:$PATH"
echo "    uv $(uv --version)"

echo "==> [4/9] Backend Python 3.13 + deps (uv managed, no system python change)"
cd "$ROOT/backend"
uv python install 3.13 >/dev/null
uv sync --quiet
PY="backend/.venv/bin/python"
cd "$ROOT"
echo "    $($PY --version)"

echo "==> [5/9] Claude CLI (research agent subprocess)"
if ! command -v claude >/dev/null 2>&1; then
  curl -fsSL https://claude.ai/install.sh | bash \
    || echo "  (claude install failed — research will be unavailable; check network)"
fi

if [ "$SKIP_BUILD" = "0" ]; then
  echo "==> [6/9] Webui same-origin config (apiBase/BACKEND empty → relative)"
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

  echo "==> [7/9] Webui build (tsc -b && vite build) — may take ~30-60s"
  export NODE_OPTIONS="--max-old-space-size=4096"
  (cd webui && npm ci --silent && npm run build)
  echo "    dist: $(find webui/dist -mindepth 1 -maxdepth 1 | wc -l) entries"
else
  echo "==> [6-7/9] Skipped webui build (DIM0_SKIP_BUILD=1) — upload dist to $INSTALL_DIR/webui/dist"
fi

echo "==> [8/9] Install app to $INSTALL_DIR"
sudo mkdir -p "$INSTALL_DIR"
# rsync if available (excludes .git/.venv/node_modules — venv rebuilt below),
# else cp.
if command -v rsync >/dev/null 2>&1; then
  sudo rsync -a --delete \
    --exclude '.git' --exclude 'backend/.venv' --exclude 'webui/node_modules' \
    "$ROOT/" "$INSTALL_DIR/"
else
  sudo cp -a "$ROOT"/* "$INSTALL_DIR/"
fi
sudo chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR"
# Recreate the backend venv in-place at the install location (the rsync above
# excludes backend/.venv, and venvs aren't reliably relocatable by copy).
# uv reuses its download cache, so this is fast on a re-run.
(cd "$INSTALL_DIR/backend" && uv sync --quiet)
PY_INSTALL="$INSTALL_DIR/backend/.venv/bin/python"
if [ ! -x "$PY_INSTALL" ]; then
  echo "  ERROR: $PY_INSTALL not created — backend won't start" >&2
else
  echo "  $($PY_INSTALL --version) at $PY_INSTALL"
fi

echo "==> [9/9] systemd unit + nginx site"
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