# Deploy dim0 on AWS EC2 (single-origin, production)

This deploys dim0 on a single EC2 instance: the FastAPI backend serves the
built webui (launcher + canvas) at one public URL, with Postgres/Qdrant/Redis
as external managed services (Neon / Qdrant Cloud / Upstash). nginx reverse-
proxies and handles WebSocket (collab canvas) + SSE (research streaming).

Architecture:

```
Internet → nginx :80 (public IP) → backend uvicorn 127.0.0.1:8080
                                   └─ serves webui/dist (DIM0_WEBUI_DIST)
                                      /launcher.html  = research launcher
                                      /               = canvas (SPA)
                                      /boards/{id}    = SPA shell
                                      /boards/{id}/collab     = WS
                                      /integration/research/* = SSE
External: Neon (Postgres) · Qdrant Cloud · Upstash (Redis) — via /etc/dim0/env
systemd:  dim0-backend.service (Restart=always)
```

HTTP-only until you add a domain (see **Add HTTPS later**). The app works
fully over HTTP; only the PWA service worker (installable/offline) needs
HTTPS — canvas, research, and collab do not.

## Prerequisites

- An AWS account.
- External DBs already created with keys:
  - **Neon** (Postgres) — `postgresql://USER:PW@HOST:5432/DB`
  - **Qdrant Cloud** — host + API key
  - **Upstash** (Redis) — host + password (TLS required)
- An **Ollama Cloud** API key (for AI: research + canvas chat).
- (Optional) **Tavily** API key for grounded web evidence.

## Step 1 — Launch the EC2 instance

1. EC2 → **Launch instance**.
2. **Name**: `dim0`.
3. **AMI**: Ubuntu 24.04 LTS (x86_64).
4. **Instance type**: `t3.medium` (4 GB RAM — needed for the vite build).
5. **Key pair**: create or pick an existing one (SSH key).
6. **Network settings** → Security group:
   - Allow **SSH (22)** from your IP.
   - Allow **HTTP (80)** from anywhere (`0.0.0.0/0`).
   - (Add HTTPS 443 later when you add a domain.)
7. **Storage**: 20 GB gp3 is plenty.
8. Launch. Note the **Public IPv4 address**.

## Step 2 — SSH in and clone the repo

```bash
ssh -i <your-key>.pem ubuntu@<EC2-PUBLIC-IP>
sudo apt-get update -qq && sudo apt-get install -y -qq git
git clone https://github.com/thangngny/dim0.git
cd dim0
```

## Step 3 — Run the bootstrap script

```bash
bash deploy/aws/setup.sh
```

This installs nginx, Node 22, uv + Python 3.13, the Claude CLI, builds the
webui same-origin, copies the app to `/opt/dim0`, and installs the systemd
unit + nginx site. It does **not** start the backend yet (it needs the env
file first). At the end it prints the next steps.

## Step 4 — Fill in `/etc/dim0/env` with your real credentials

```bash
sudo nano /etc/dim0/env
```

Set the real values for the sections marked `replace-me`:

- `POSTGRES_*` — from your Neon connection string.
- `QDRANT_*` — from Qdrant Cloud.
- `REDIS_*` — from Upstash (keep `REDIS_SSL=true`).
- `JWT_SECRET_KEY` — generate one: `python3 -c "import secrets;print(secrets.token_hex(32))"`.
- `DIM0_INTEGRATION_TOKEN` — **must match** the `TOKEN` in
  `webui/public/launcher.html` (currently
  `b82baa54d8ff5b6e242d2894dbe83bc812e5a3bbae07a348aacdd7ba29200e6d`).
  If you change it here, change `launcher.html` too or research calls 401.
- `ANTHROPIC_AUTH_TOKEN` / `OLLAMA_API_KEY` — your Ollama Cloud key.
- `TAVILY_API_KEY` — optional.

`DIM0_WEBUI_DIST` and `PORT` are already patched by `setup.sh` — leave them.

Save and exit.

## Step 5 — Start the backend

```bash
sudo systemctl enable --now dim0-backend
sudo systemctl status dim0-backend     # should be active (running)
```

If it fails, check the log:

```bash
sudo tail -50 /var/log/dim0-backend.log
```

Common causes: a DB credential is wrong, or `DIM0_INTEGRATION_TOKEN` doesn't
match `launcher.html`. Fix `/etc/dim0/env` then
`sudo systemctl restart dim0-backend`.

## Step 6 — Verify

From the instance (or your laptop, using the public IP):

```bash
# Backend health
curl http://localhost/integration/health
# → {"status":"ok","agent_bridge":true,...}

# Launcher + canvas are served
curl -I http://localhost/launcher.html    # 200
curl -I http://localhost/                 # 200
```

Then in a browser:

- **Launcher**: `http://<EC2-PUBLIC-IP>/launcher.html` — ask a research
  question.
- **Canvas**: `http://<EC2-PUBLIC-IP>/` — sign up, create a board; the canvas
  loads nodes over the collab WebSocket (needs Upstash Redis).

Full end-to-end smoke test (signup → board → collab ticket → WS → research):

```bash
IP=<EC2-PUBLIC-IP>
# signup (use a real email domain; .local is rejected)
curl -s -X POST http://$IP/users/signup -H 'Content-Type: application/json' \
  -d '{"email":"you@mailtest.com","password":"Pass123!","name":"You","username":"you"}'
# signin (form-encoded OAuth2 password flow)
TOKEN=$(curl -s -X POST http://$IP/users/signin \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d 'username=you@mailtest.com&password=Pass123!' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['data']['token']['access_token'])")
# create a board + mint a collab ticket (tests Redis)
GID=$(curl -s -X PUT http://$IP/boards -H "Authorization: Bearer $TOKEN" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['data']['graph_id'])")
curl -s -X POST http://$IP/boards/$GID/collab/ticket -H "Authorization: Bearer $TOKEN"
# research clarify (tests Ollama Cloud LLM)
ITOK=b82baa54d8ff5b6e242d2894dbe83bc812e5a3bbae07a348aacdd7ba29200e6d
curl -s -X POST http://$IP/integration/research/clarify \
  -H "X-Integration-Token: $ITOK" -H 'Content-Type: application/json' \
  -d '{"topic":"Impact of AI on education","language":"en","stage":"questions"}'
```

## Updating the app after a code change

```bash
cd dim0
git pull
bash deploy/aws/setup.sh          # rebuilds webui, re-copies to /opt/dim0
sudo systemctl restart dim0-backend
```

`setup.sh` is safe to re-run (it overwrites `/opt/dim0` and the unit/nginx
configs, but preserves `/etc/dim0/env`).

## Add HTTPS later (when you have a domain)

1. Point an **A record** for your domain → EC2 public IP.
2. Open **HTTPS (443)** in the security group.
3. On the instance:

   ```bash
   sudo apt-get install -y certbot python3-certbot-nginx
   sudo certbot --nginx -d yourdomain.com
   ```

   certbot edits the nginx site to add the 443 listener + TLS cert and
   redirects 80→443. WebSocket upgrades to `wss://` automatically.

4. `sudo systemctl reload nginx`.

## Files in this directory

| File | Purpose |
|---|---|
| `setup.sh` | One-shot bootstrap (run on the instance). |
| `dim0-backend.service` | systemd unit template (placeholders substituted by `setup.sh`). |
| `nginx/dim0.conf` | nginx site template (placeholders substituted by `setup.sh`). |
| `env.example` | Template for `/etc/dim0/env` (DB + AI secrets). |

## Troubleshooting

- **`systemctl status` shows crash / restart loop**: `sudo tail -50 /var/log/dim0-backend.log`. Almost always a wrong DB credential or a missing `*_SSL` flag.
- **vite build OOMs during `setup.sh`**: `t3.medium` has 4 GB and `NODE_OPTIONS=--max-old-space-size=4096` is set; if it still OOMs, build locally (`cd webui && NODE_OPTIONS=--max-old-space-size=4096 npm run build`) and `scp -r webui/dist` to `/opt/dim0/webui/dist` on the instance, then `sudo systemctl restart dim0-backend`.
- **Research returns "claude CLI not found"**: the `curl https://claude.ai/install.sh` step in `setup.sh` failed (network). Re-run it manually, ensure `claude` is on `PATH` for the `ubuntu` user, then restart the backend.
- **Canvas is empty**: Upstash Redis isn't reachable. Confirm `REDIS_HOST/PORT/PASSWORD` and `REDIS_SSL=true` in `/etc/dim0/env`, then restart. The collab ticket endpoint returns 500 when Redis is down.
- **`/launcher.html` 404**: `DIM0_WEBUI_DIST` doesn't point at a built dist. Confirm `/opt/dim0/webui/dist/launcher.html` exists.