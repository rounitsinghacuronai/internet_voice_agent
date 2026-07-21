#!/usr/bin/env bash
#
# Deploy the Syncbroad admin dashboard (Next.js) at
#   https://internet.acuronai.com/admin
#
# Runs on the SAME EC2 box as the backend, AFTER deploy/deploy_internet.sh has
# set up the backend + the internet.acuronai.com nginx vhost + TLS cert.
#
# It is deliberately NON-DESTRUCTIVE and IDEMPOTENT (safe to re-run):
#   • builds the dashboard as a systemd service on 127.0.0.1:3100
#   • adds ONE `location /admin` into the EXISTING :443 vhost (never a new cert,
#     never a new server block, never touches other sites)
#   • reuses the existing `$connection_upgrade` map from the backend vhost
#
#   chmod +x deploy/deploy_dashboard.sh
#   sudo ./deploy/deploy_dashboard.sh
#
set -euo pipefail

# ── config (override via env) ────────────────────────────────────────────────
DOMAIN="${DOMAIN:-internet.acuronai.com}"
APP_DIR="${APP_DIR:-/opt/syncbroad-internet}"
DASH_DIR="${DASH_DIR:-$APP_DIR/frontend/dashboard}"
SERVICE="${SERVICE:-syncbroad-dashboard}"
APP_USER="${APP_USER:-${SUDO_USER:-ubuntu}}"
DASH_PORT="${DASH_PORT:-3100}"
BASE_PATH="${BASE_PATH:-/admin}"
VHOST="${VHOST:-/etc/nginx/sites-available/${DOMAIN}}"
SNIPPET="/etc/nginx/snippets/syncbroad-admin.conf"

echo "==> 0/6  Pre-flight"
echo "    domain    : ${DOMAIN}${BASE_PATH}"
echo "    dash dir  : ${DASH_DIR}"
echo "    service   : ${SERVICE}.service  (127.0.0.1:${DASH_PORT})"
echo "    run user  : ${APP_USER}"
[ -d "$DASH_DIR" ] || { echo "!! ${DASH_DIR} not found — sync the repo to ${APP_DIR} first"; exit 1; }
[ -f "$VHOST" ]    || { echo "!! ${VHOST} not found — run deploy/deploy_internet.sh first"; exit 1; }

# ── 1. Node.js 18+ ───────────────────────────────────────────────────────────
echo "==> 1/6  Ensuring Node.js 18+"
need_node=1
if command -v node >/dev/null 2>&1; then
    major=$(node -p 'process.versions.node.split(".")[0]')
    [ "$major" -ge 18 ] && need_node=0
fi
if [ "$need_node" -eq 1 ]; then
    echo "    installing Node.js 20 (NodeSource)…"
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
    sudo apt-get install -y nodejs
fi
echo "    node $(node -v), npm $(npm -v)"

# ── 2. Build the dashboard ───────────────────────────────────────────────────
echo "==> 2/6  Building dashboard (base path ${BASE_PATH}, same-origin API)"
sudo chown -R "$APP_USER":"$APP_USER" "$DASH_DIR"
sudo -u "$APP_USER" bash -lc "
  cd '$DASH_DIR'
  export NEXT_PUBLIC_BASE_PATH='$BASE_PATH'
  export NEXT_PUBLIC_API_BASE=''
  export NEXT_PUBLIC_DATA_SOURCE='live'
  npm ci
  npm run build
"

# ── 3. systemd service ───────────────────────────────────────────────────────
echo "==> 3/6  Writing systemd unit"
sudo tee "/etc/systemd/system/${SERVICE}.service" >/dev/null <<UNIT
[Unit]
Description=Syncbroad Admin Dashboard (Next.js)
After=network.target syncbroad-internet.service
Wants=syncbroad-internet.service

[Service]
Type=simple
User=${APP_USER}
WorkingDirectory=${DASH_DIR}
Environment=NODE_ENV=production
Environment=PORT=${DASH_PORT}
Environment=NEXT_PUBLIC_BASE_PATH=${BASE_PATH}
Environment=NEXT_PUBLIC_API_BASE=
Environment=NEXT_PUBLIC_DATA_SOURCE=live
ExecStart=$(command -v npm) run start
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE}" >/dev/null 2>&1 || true
sudo systemctl restart "${SERVICE}"
sleep 2
sudo systemctl --no-pager --lines=0 status "${SERVICE}" || true

# ── 4. nginx location snippet ────────────────────────────────────────────────
echo "==> 4/6  Installing nginx snippet ${SNIPPET}"
sudo mkdir -p /etc/nginx/snippets
sudo tee "$SNIPPET" >/dev/null <<SNIP
location ${BASE_PATH} {
    proxy_pass         http://127.0.0.1:${DASH_PORT};
    proxy_http_version 1.1;
    proxy_set_header   Upgrade           \$http_upgrade;
    proxy_set_header   Connection        \$connection_upgrade;
    proxy_set_header   Host              \$host;
    proxy_set_header   X-Real-IP         \$remote_addr;
    proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;
    proxy_set_header   X-Forwarded-Proto \$scheme;
    proxy_read_timeout 60s;
}
SNIP

# ── 5. Wire the include into the :443 server block (idempotent) ───────────────
echo "==> 5/6  Wiring include into ${VHOST} (:443 block)"
sudo python3 - "$VHOST" "$SNIPPET" <<'PY'
import re, sys
vhost, snippet = sys.argv[1], sys.argv[2]
src = open(vhost).read()
include_line = f"    include {snippet};\n"
if snippet in src:
    print("    include already present — nothing to do")
    sys.exit(0)

# find the server block that listens on 443 and insert the include right after
# its opening brace. Walk braces to locate blocks safely.
def server_blocks(text):
    for m in re.finditer(r'server\s*\{', text):
        i = m.end(); depth = 1; j = i
        while j < len(text) and depth:
            if text[j] == '{': depth += 1
            elif text[j] == '}': depth -= 1
            j += 1
        yield m.start(), m.end(), j  # (block_start, brace_after, block_end)

patched = None
for start, brace, end in server_blocks(src):
    block = src[start:end]
    if re.search(r'listen[^;]*443', block):
        patched = src[:brace] + "\n" + include_line + src[brace:]
        break
if not patched:
    print("!! could not find a :443 server block — add this line manually inside it:")
    print(include_line.strip())
    sys.exit(1)
open(vhost, "w").write(patched)
print("    include added to :443 server block")
PY

# ── 6. Test + reload nginx ───────────────────────────────────────────────────
echo "==> 6/6  Testing + reloading nginx"
sudo nginx -t
sudo systemctl reload nginx

echo
echo "✅ Done."
echo "   Dashboard : https://${DOMAIN}${BASE_PATH}"
echo "   Service   : sudo systemctl status ${SERVICE}"
echo "   Logs      : sudo journalctl -u ${SERVICE} -f"
echo "   Re-deploy : git pull && sudo ./deploy/deploy_dashboard.sh"
