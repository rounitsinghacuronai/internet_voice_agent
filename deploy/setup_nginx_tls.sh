#!/usr/bin/env bash
#
# One-shot server setup for the Mahavitaran Voice agent.
# Configures nginx as a reverse proxy (with WebSocket support) in front of the
# FastAPI app on 127.0.0.1:8000, then installs a Let's Encrypt TLS certificate.
#
# RUN THIS ON THE EC2 UBUNTU SERVER (13.127.81.150), not on your laptop:
#     scp deploy/setup_nginx_tls.sh ubuntu@13.127.81.150:~
#     ssh ubuntu@13.127.81.150
#     chmod +x setup_nginx_tls.sh && ./setup_nginx_tls.sh
#
# Safe to re-run (idempotent).

set -euo pipefail

DOMAIN="voiceagent.acuronai.com"
APP_PORT="8000"
LE_EMAIL="anuragparihar084@gmail.com"   # Let's Encrypt renewal notices

echo "==> 1/6  Checking the app is actually listening on 127.0.0.1:${APP_PORT}"
if curl -fsS "http://127.0.0.1:${APP_PORT}/health" >/dev/null 2>&1; then
    echo "    OK — /health responded."
else
    echo "    WARNING: nothing answered on 127.0.0.1:${APP_PORT}/health."
    echo "    The FastAPI app may not be running. Check its systemd service, e.g.:"
    echo "        systemctl list-units --type=service | grep -iE 'voice|uvicorn|fastapi'"
    echo "        sudo systemctl status <that-service>"
    echo "    Continuing anyway — nginx will proxy once the app is up."
fi

echo "==> 2/6  Installing nginx + certbot (if missing)"
sudo apt-get update -y
sudo apt-get install -y nginx certbot python3-certbot-nginx curl

echo "==> 3/6  Writing WebSocket upgrade map (http context)"
sudo tee /etc/nginx/conf.d/websocket_upgrade.conf >/dev/null <<'EOF'
# Maps the Upgrade header so nginx proxies WebSocket connections correctly.
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}
EOF

echo "==> 4/6  Writing server block for ${DOMAIN}"
sudo tee "/etc/nginx/sites-available/${DOMAIN}" >/dev/null <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};

    # Reverse-proxy everything to the FastAPI app. WebSocket routes
    # (/ws/call, /ws/exotel) work because of the Upgrade/Connection headers.
    location / {
        proxy_pass http://127.0.0.1:${APP_PORT};
        proxy_http_version 1.1;

        proxy_set_header Host              \$host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;

        # WebSocket upgrade
        proxy_set_header Upgrade    \$http_upgrade;
        proxy_set_header Connection \$connection_upgrade;

        # Voice/Exotel streams are long-lived — don't time them out or buffer them.
        proxy_read_timeout  3600s;
        proxy_send_timeout  3600s;
        proxy_buffering     off;
    }
}
EOF

# Enable this site, disable the stock default that's currently serving the
# "Welcome to nginx" placeholder page.
sudo ln -sf "/etc/nginx/sites-available/${DOMAIN}" "/etc/nginx/sites-enabled/${DOMAIN}"
sudo rm -f /etc/nginx/sites-enabled/default

echo "==> 5/6  Testing and reloading nginx"
sudo nginx -t
sudo systemctl reload nginx
echo "    nginx reloaded. Plain-HTTP proxy is live on port 80."

echo "==> 6/6  Requesting TLS certificate from Let's Encrypt"
# certbot needs the domain to resolve to THIS server and port 80 reachable
# from the internet. Verify DNS first so we fail with a clear message.
RESOLVED_IP="$(getent hosts "${DOMAIN}" | awk '{print $1}' | head -n1 || true)"
if [ -z "${RESOLVED_IP}" ]; then
    echo "    SKIPPED: ${DOMAIN} does not resolve yet (DNS not propagated)."
    echo "    Once 'getent hosts ${DOMAIN}' returns 13.127.81.150, run:"
    echo "        sudo certbot --nginx -d ${DOMAIN} --agree-tos -m ${LE_EMAIL} --redirect --non-interactive"
    echo ""
    echo "    HTTP proxy is already working. Finish TLS after DNS is live."
    exit 0
fi
echo "    ${DOMAIN} resolves to ${RESOLVED_IP}. Requesting certificate..."
sudo certbot --nginx -d "${DOMAIN}" --agree-tos -m "${LE_EMAIL}" --redirect --non-interactive

echo ""
echo "==> DONE."
echo "    Test:  curl -I https://${DOMAIN}/health"
echo "    Then point the Exotel Voicebot applet at:"
echo "        wss://${DOMAIN}/ws/exotel?sample-rate=16000"
