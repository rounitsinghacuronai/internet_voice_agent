# Deploy runbook — https://internet.acuronai.com

Deploys this project as a **new, isolated instance** on the EC2 box that already
runs nginx in front of your other projects (e.g. `voiceagent.acuronai.com`).
Nothing here touches the other sites: it adds its own port, its own systemd
service, and a name-based nginx vhost that only matches `internet.acuronai.com`.

Server IP referenced below: **13.127.81.150** — change it if yours differs.

Total hands-on time ≈ 10 minutes, most of it waiting for DNS.

---

## What gets created (and what is left alone)

| Created (new, isolated) | Left untouched |
|---|---|
| `/opt/syncbroad-internet/` — app code + `.venv` | Every other app's files and ports |
| `syncbroad-internet.service` — systemd unit on a **free** loopback port (auto-picked, first tries 8010) | `voiceagent`'s service on :8000 and the other projects |
| `/etc/nginx/sites-available/internet.acuronai.com` + symlink | `sites-enabled/default` and all other vhosts |
| A Let's Encrypt cert **for internet.acuronai.com only** | The other domains' certs |
| The shared `$http_upgrade` map **only if one doesn't already exist** | An existing WebSocket map is reused, not redefined |

The port is chosen at run time by scanning listening sockets, so it can never
land on a port another project is using.

---

## Step 1 — Point DNS at the server (do this first; it takes time to propagate)

In your DNS provider (Route 53 / wherever `acuronai.com` is managed), add:

```
Type: A    Name: internet    Value: 13.127.81.150    TTL: 300
```

Check from your laptop until it resolves to the server:

```bash
dig +short internet.acuronai.com     # should print 13.127.81.150
```

TLS is issued automatically once this resolves. If you run the deploy before DNS
is live, the site still works over HTTP and the script prints the one certbot
command to finish TLS later.

## Step 2 — (recommended) prep the server-side `.env`

The app reads `.env` from the project root. Your local `.env` has the API keys,
so it will be uploaded in Step 3. Two things worth setting for a **server** run:

- `WHATSAPP_ENABLED=false` — the WhatsApp bridge is a separate desktop sidecar
  that needs a QR scan; leave it off on the server unless you run the bridge
  there. (With it on but no bridge, notifications just retry and give up — the
  call is unaffected, but the logs get noisy.)
- Streaming STT/TTS will actually engage on the server because `requirements.txt`
  installs the `sarvamai` package (locally you saw it fall back to REST because
  that package wasn't installed).

You can edit `.env` now locally, or on the server after Step 3.

## Step 3 — Upload the code from your Mac

Create the target dir (owned by your login user) and rsync the project up.
Run this **from your laptop**, in the project folder:

```bash
# 3a. make the destination writable by your SSH user
ssh ubuntu@13.127.81.150 'sudo mkdir -p /opt/syncbroad-internet && sudo chown $USER:$USER /opt/syncbroad-internet'

# 3b. copy the project (excludes local venv, git, caches, WhatsApp session)
cd "/Users/raunitsingh/Documents/Telecom Ai voice agent"
rsync -avz \
  --exclude '.venv' --exclude '.git' --exclude '__pycache__' \
  --exclude '.pytest_cache' --exclude 'whatsapp_bridge/session' \
  --exclude 'whatsapp_bridge/node_modules' \
  ./ ubuntu@13.127.81.150:/opt/syncbroad-internet/
```

The `.env` (with your keys) IS included by rsync — that's intended for your own
server.

## Step 4 — Run the deploy script on the server

```bash
ssh ubuntu@13.127.81.150
cd /opt/syncbroad-internet
chmod +x deploy/deploy_internet.sh
./deploy/deploy_internet.sh
```

The script prints a pre-flight (current ports + existing nginx sites), picks a
free port, builds the venv, starts the service, wires nginx, and — if DNS is
live — issues the TLS cert. It is safe to re-run.

## Step 5 — Verify

```bash
# on the server
sudo systemctl status syncbroad-internet --no-pager
curl -s http://127.0.0.1:8010/health        # use the port the script reported

# from anywhere
curl -I https://internet.acuronai.com/health
```

Then open **https://internet.acuronai.com**, click **Start Call**, and speak.
To test caller-ID recognition, type a registered number into the “Calling from…”
box (e.g. `8624900039` → greets “नमस्कार Kiran…”, or `7267850755` → “नमस्कार Rounit…”).

## Step 6 — Point Exotel at it (if using telephony)

In App Bazaar, set the Voicebot applet URL to:

```
wss://internet.acuronai.com/ws/exotel?sample-rate=16000
```

---

## Managing the instance

```bash
sudo systemctl restart syncbroad-internet     # restart
sudo systemctl stop syncbroad-internet        # stop
sudo journalctl -u syncbroad-internet -f      # live logs
```

## Updating the app later

Re-run the rsync from Step 3b, then:

```bash
ssh ubuntu@13.127.81.150 'sudo systemctl restart syncbroad-internet'
```

(If `requirements.txt` changed, first:
`/opt/syncbroad-internet/.venv/bin/pip install -r /opt/syncbroad-internet/requirements.txt`.)

## Rollback / full removal (does not affect other projects)

```bash
sudo systemctl disable --now syncbroad-internet
sudo rm /etc/systemd/system/syncbroad-internet.service
sudo systemctl daemon-reload
sudo rm /etc/nginx/sites-enabled/internet.acuronai.com \
        /etc/nginx/sites-available/internet.acuronai.com
sudo nginx -t && sudo systemctl reload nginx
sudo rm -rf /opt/syncbroad-internet
# optional: sudo certbot delete --cert-name internet.acuronai.com
```

---

## Collision safety — why the other two projects stay up

- **Ports:** the app binds `127.0.0.1:<free-port>` only (never `0.0.0.0`, never a
  port already in use). nginx is the only public listener.
- **nginx:** the new vhost is name-based (`server_name internet.acuronai.com`)
  and is **not** a `default_server`, so requests for the other domains keep
  hitting their own vhosts. The script never deletes `default` or other sites.
- **WebSocket map:** redefining `map $http_upgrade …` is the classic way a second
  project breaks nginx for everyone (`nginx -t` → "duplicate map"). The script
  detects an existing map and reuses it instead of writing a second one.
- **TLS:** certbot is scoped to `-d internet.acuronai.com`, so other certs are
  not renewed or altered.
- **State:** its own `/opt` dir, `.env`, `telecom.db`, and logs — no shared
  files with the other instances.

If anything looks off before reloading, `sudo nginx -t` validates the whole
config; the script runs it and will abort the reload on any error.
