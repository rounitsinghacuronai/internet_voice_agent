#!/usr/bin/env bash
#
# Set up the personal-account WhatsApp bridge on the EC2 server, so the voice
# agent can post ops notifications FROM YOUR OWN WhatsApp number.
#
# It installs Node.js + a real system Chrome (reliable headless, unlike
# puppeteer's bundled Chromium), installs the bridge's npm deps, and writes a
# systemd service. You scan the login QR ONCE (it renders as text in the
# terminal), after which the saved session survives restarts.
#
# Run on the server:
#     cd /opt/syncbroad-internet
#     chmod +x deploy/setup_whatsapp_bridge.sh
#     ./deploy/setup_whatsapp_bridge.sh
#
# Safe to re-run.
#
# ⚠️  ToS note: automating a *personal* WhatsApp account is against WhatsApp's
#     terms and risks a ban. For anything beyond a POC use a dedicated number,
#     or switch to WHATSAPP_PROVIDER=meta (Cloud API).
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/syncbroad-internet}"
BRIDGE_DIR="${BRIDGE_DIR:-${APP_DIR}/whatsapp_bridge}"
SERVICE="${SERVICE:-syncbroad-wa-bridge}"
APP_USER="${APP_USER:-${SUDO_USER:-ubuntu}}"
CHROME_BIN="/usr/bin/google-chrome-stable"

echo "==> 1/5  Node.js"
if ! command -v node >/dev/null 2>&1 || [ "$(node -v | sed 's/v//; s/\..*//')" -lt 18 ]; then
    echo "    installing Node.js 20…"
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
    sudo apt-get install -y nodejs
else
    echo "    OK — $(node -v)"
fi

echo "==> 2/5  System Chrome (brings all headless libraries as dependencies)"
if [ ! -x "${CHROME_BIN}" ]; then
    tmp="$(mktemp --suffix=.deb)"
    wget -qO "${tmp}" https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
    sudo apt-get update -y
    sudo apt-get install -y "${tmp}"
    rm -f "${tmp}"
else
    echo "    OK — ${CHROME_BIN} present"
fi

echo "==> 3/5  Bridge npm dependencies"
if [ ! -f "${BRIDGE_DIR}/index.js" ]; then
    echo "    ERROR: ${BRIDGE_DIR}/index.js not found — is the repo synced to ${APP_DIR}?"
    exit 1
fi
cd "${BRIDGE_DIR}"
# Skip puppeteer's own Chromium download — we use system Chrome above.
sudo -u "${APP_USER}" env PUPPETEER_SKIP_DOWNLOAD=true \
     PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true npm install --no-audit --no-fund

echo "==> 4/5  systemd unit /etc/systemd/system/${SERVICE}.service"
sudo tee "/etc/systemd/system/${SERVICE}.service" >/dev/null <<EOF
[Unit]
Description=Syncbroad WhatsApp Bridge (personal account) — ops notifications
After=network.target

[Service]
Type=simple
User=${APP_USER}
WorkingDirectory=${BRIDGE_DIR}
Environment=PUPPETEER_EXECUTABLE_PATH=${CHROME_BIN}
Environment=BRIDGE_HEADLESS=true
Environment=BRIDGE_PORT=3001
ExecStart=/usr/bin/node index.js
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload

echo "==> 5/5  Setup complete — now do the ONE-TIME QR login:"
cat <<EOF

  A) Scan the QR once, in the foreground (most reliable):

        cd ${BRIDGE_DIR}
        PUPPETEER_EXECUTABLE_PATH=${CHROME_BIN} node index.js

     A QR code prints in the terminal. On your phone: WhatsApp →
     Settings → Linked Devices → Link a device → scan it.
     Wait for "[bridge] READY — WhatsApp session live", then press Ctrl-C.
     (The login is saved to ${BRIDGE_DIR}/session — no re-scan on restart.)

  B) Start it as a background service:

        sudo systemctl enable --now ${SERVICE}
        sudo systemctl status ${SERVICE} --no-pager
        curl -s http://127.0.0.1:3001/health      # {"ready":true,"state":"ready"}

  C) Turn it on for the voice app — edit ${APP_DIR}/.env:

        WHATSAPP_ENABLED=true
        WHATSAPP_PROVIDER=personal
        WHATSAPP_BRIDGE_URL=http://127.0.0.1:3001
        WHATSAPP_GROUP_NAME=120363427248805027@g.us   # your ops group id,
        # …or DM yourself:  WHATSAPP_GROUP_NAME=<countrycode><number>@c.us
        # e.g. 917267850755@c.us

     Then: sudo systemctl restart syncbroad-internet

EOF
