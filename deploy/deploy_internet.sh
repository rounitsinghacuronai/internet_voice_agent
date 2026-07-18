#!/usr/bin/env bash
#
# Collision-safe deploy for the Syncbroad Networks Voice agent at
#   https://internet.acuronai.com
#
# Designed for an EC2 box that ALREADY runs nginx in front of other projects.
# It is deliberately NON-DESTRUCTIVE:
#   • picks a FREE loopback port (never assumes 8000 is free)
#   • adds a NAME-BASED nginx vhost (server_name internet.acuronai.com) that
#     coexists with the other domains on :443 — it is NOT a default_server
#   • REUSES an existing `map $http_upgrade $connection_upgrade` if one is
#     already defined (redefining it would make `nginx -t` fail with a
#     "duplicate map" error and take every site down)
#   • never touches sites-enabled/default or any other project's config
#   • requests a TLS cert for THIS domain only
#
# Run on the server AFTER the code has been synced to APP_DIR (see the runbook
# deploy/DEPLOY_INTERNET_RUNBOOK.md). Safe to re-run (idempotent).
#
#     chmod +x deploy/deploy_internet.sh
#     ./deploy/deploy_internet.sh
#
set -euo pipefail

# ── config (override via env: DOMAIN=… ./deploy_internet.sh) ─────────────────
DOMAIN="${DOMAIN:-internet.acuronai.com}"
APP_DIR="${APP_DIR:-/opt/syncbroad-internet}"
SERVICE="${SERVICE:-syncbroad-internet}"
APP_USER="${APP_USER:-${SUDO_USER:-ubuntu}}"
LE_EMAIL="${LE_EMAIL:-anuragparihar084@gmail.com}"   # Let's Encrypt renewal notices
PREFERRED_PORT="${APP_PORT:-8010}"                   # first choice; auto-bumped if taken

echo "==> 0/8  Pre-flight (nothing is changed yet)"
echo "    domain      : ${DOMAIN}"
echo "    app dir     : ${APP_DIR}"
echo "    service     : ${SERVICE}.service"
echo "    run as user : ${APP_USER}"
echo "    currently listening TCP sockets:"
ss -ltnH 2>/dev/null | awk '{print "        "$4}' | sort -u || true
echo "    existing nginx sites:"
ls -1 /etc/nginx/sites-enabled 2>/dev/null | sed 's/^/        /' || true

# ── pick a free loopback port so we never collide with another app ───────────
port_in_use() { ss -ltnH 2>/dev/null | awk '{print $4}' | sed 's/.*://' | grep -qx "$1"; }
APP_PORT="$PREFERRED_PORT"
while port_in_use "$APP_PORT"; do
    echo "    port ${APP_PORT} is busy — trying $((APP_PORT+1))"
    APP_PORT=$((APP_PORT+1))
done
echo "    selected app port: ${APP_PORT} (bound to 127.0.0.1 only)"

# ── code must already be on the box ──────────────────────────────────────────
echo "==> 1/8  Verifying app code is present in ${APP_DIR}"
if [ ! -f "${APP_DIR}/backend/app/main.py" ]; then
    echo "    ERROR: ${APP_DIR}/backend/app/main.py not found."
    echo "    Sync the project first (from your laptop), e.g.:"
    echo "        rsync -avz --exclude .venv --exclude .git --exclude __pycache__ \\"
    echo "              --exclude 'whatsapp_bridge/session' \\"
    echo "              './' ${APP_USER}@<server>:${APP_DIR}/"
    exit 1
fi
if [ ! -f "${APP_DIR}/.env" ]; then
    echo "    WARNING: ${APP_DIR}/.env is missing — the app needs SARVAM_API_KEY and"
    echo "    GEMINI_API_KEY. Copy your .env up before starting the service."
fi

echo "==> 2/8  Installing system packages (nginx, certbot, python venv)"
sudo apt-get update -y
sudo apt-get install -y python3-venv python3-pip nginx certbot python3-certbot-nginx curl

echo "==> 3/8  Python venv + dependencies (isolated in ${APP_DIR}/.venv)"
sudo mkdir -p "${APP_DIR}"
sudo chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"
sudo -u "${APP_USER}" python3 -m venv "${APP_DIR}/.venv"
sudo -u "${APP_USER}" "${APP_DIR}/.venv/bin/pip" install -q --upgrade pip
sudo -u "${APP_USER}" "${APP_DIR}/.venv/bin/pip" install -q -r "${APP_DIR}/requirements.txt"

echo "==> 4/8  systemd unit /etc/systemd/system/${SERVICE}.service"
sudo tee "/etc/systemd/system/${SERVICE}.service" >/dev/null <<EOF
[Unit]
Description=Syncbroad Networks Voice — ${DOMAIN}
After=network.target

[Service]
Type=simple
User=${APP_USER}
WorkingDirectory=${APP_DIR}
# Bind to loopback only; nginx is the public front door.
ExecStart=${APP_DIR}/.venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port ${APP_PORT}
Restart=on-failure
RestartSec=3
# Long-lived voice sockets — give the process room and a clean stop.
KillSignal=SIGINT
TimeoutStopSec=20

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE}" >/dev/null 2>&1 || true
sudo systemctl restart "${SERVICE}"

echo "    waiting for the app to answer on 127.0.0.1:${APP_PORT}…"
for i in $(seq 1 20); do
    if curl -fsS "http://127.0.0.1:${APP_PORT}/health" >/dev/null 2>&1; then
        echo "    OK — /health responded."
        break
    fi
    sleep 1
    if [ "$i" = "20" ]; then
        echo "    WARNING: app not answering yet. Check: sudo journalctl -u ${SERVICE} -n 50 --no-pager"
    fi
done

echo "==> 5/8  WebSocket upgrade map (reuse if the box already has one)"
if sudo nginx -T 2>/dev/null | grep -qE 'map[[:space:]]+\$http_upgrade'; then
    echo "    An \$http_upgrade map already exists — reusing \$connection_upgrade."
else
    sudo tee /etc/nginx/conf.d/websocket_upgrade.conf >/dev/null <<'EOF'
# Shared by all vhosts that proxy WebSockets. Defined once, http context.
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}
EOF
    echo "    Created /etc/nginx/conf.d/websocket_upgrade.conf"
fi

echo "==> 6/8  nginx vhost for ${DOMAIN} (name-based; other sites untouched)"
sudo tee "/etc/nginx/sites-available/${DOMAIN}" >/dev/null <<EOF
# Syncbroad Networks Voice — ${DOMAIN}
# Name-based virtual host: matches only this Host header, so it coexists with
# the other projects already served on this IP. NOT a default_server.
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};

    location / {
        proxy_pass http://127.0.0.1:${APP_PORT};
        proxy_http_version 1.1;

        proxy_set_header Host              \$host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;

        # WebSocket upgrade (/ws/call browser client, /ws/exotel phone leg)
        proxy_set_header Upgrade    \$http_upgrade;
        proxy_set_header Connection \$connection_upgrade;

        # Voice/Exotel streams are long-lived — no timeouts, no buffering.
        proxy_read_timeout  3600s;
        proxy_send_timeout  3600s;
        proxy_buffering     off;
    }
}
EOF
sudo ln -sf "/etc/nginx/sites-available/${DOMAIN}" "/etc/nginx/sites-enabled/${DOMAIN}"
# NOTE: intentionally do NOT remove sites-enabled/default or any other site.

echo "==> 7/8  Testing and reloading nginx"
sudo nginx -t
sudo systemctl reload nginx
echo "    nginx reloaded — HTTP proxy for ${DOMAIN} is live."

echo "==> 8/8  TLS certificate (this domain only)"
RESOLVED_IP="$(getent hosts "${DOMAIN}" | awk '{print $1}' | head -n1 || true)"
if [ -z "${RESOLVED_IP}" ]; then
    echo "    SKIPPED: ${DOMAIN} does not resolve yet (add the DNS A record first)."
    echo "    Once 'getent hosts ${DOMAIN}' returns this server's IP, run:"
    echo "        sudo certbot --nginx -d ${DOMAIN} --agree-tos -m ${LE_EMAIL} --redirect --non-interactive"
else
    echo "    ${DOMAIN} resolves to ${RESOLVED_IP}. Requesting certificate…"
    sudo certbot --nginx -d "${DOMAIN}" --agree-tos -m "${LE_EMAIL}" --redirect --non-interactive
fi

echo ""
echo "==> DONE.  App: ${SERVICE}.service on 127.0.0.1:${APP_PORT}"
echo "    Web UI   : https://${DOMAIN}"
echo "    Health   : curl -I https://${DOMAIN}/health"
echo "    Exotel   : wss://${DOMAIN}/ws/exotel?sample-rate=16000"
echo "    Logs     : sudo journalctl -u ${SERVICE} -f"
