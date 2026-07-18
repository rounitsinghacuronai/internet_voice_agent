/**
 * TelecomCare WhatsApp Bridge (POC)
 * ---------------------------------
 * Holds ONE logged-in WhatsApp Web session (personal account) and exposes:
 *   GET  /health  → { ready, state, group_cached }
 *   POST /send    → { group, message } → { ok, id } | { ok:false, error }
 *
 * Design rules:
 *  - Session persistence: LocalAuth stores credentials in ./session — the QR
 *    scan happens ONCE; restarts reuse the session.
 *  - Auto-reconnection: 'disconnected' → exponential backoff re-initialize.
 *  - No business logic: text in, message out. Formatting/dedupe/retry live in
 *    the Python Notification Service.
 *  - Group id cached after first lookup (name → id) to avoid re-scanning
 *    chats on every send.
 *
 * ToS note: automating a personal account is against WhatsApp's terms; use a
 * dedicated number for the POC and migrate to the Business API for prod.
 */
const express = require('express');
const qrcode = require('qrcode-terminal');
const { Client, LocalAuth } = require('whatsapp-web.js');

const PORT = process.env.BRIDGE_PORT || 3001;
const HEADLESS = (process.env.BRIDGE_HEADLESS || 'true') === 'true';

let ready = false;
let state = 'starting';
let groupIdCache = new Map();        // group name (lower) → chat id
let reconnectDelay = 5000;           // grows to 60s max

const client = new Client({
  authStrategy: new LocalAuth({ dataPath: './session' }),
  puppeteer: {
    headless: HEADLESS,
    args: ['--no-sandbox', '--disable-setuid-sandbox',
           '--disable-dev-shm-usage'],
  },
});

client.on('qr', (qr) => {
  state = 'waiting_for_qr_scan';
  console.log('\n[bridge] Scan this QR with the POC WhatsApp account:\n');
  qrcode.generate(qr, { small: true });
});

client.on('authenticated', () => { state = 'authenticated'; console.log('[bridge] authenticated (session saved to ./session)'); });
client.on('ready', () => {
  ready = true; state = 'ready'; reconnectDelay = 5000;
  console.log('[bridge] READY — WhatsApp session live');
});

// GROUP DISCOVERY WITHOUT getChats(): getChats() breaks whenever WhatsApp
// changes its internal (minified) module layout. Instead, learn group ids
// passively — any message arriving in any group teaches us its name→id.
// Send one message in the ops group from your phone and the bridge knows it.
function learnGroup(msg) {
  try {
    const remote = msg.id ? msg.id.remote : (msg.from || '');
    const rid = typeof remote === 'string' ? remote : (remote._serialized || '');
    if (!rid.endsWith('@g.us')) return;
    msg.getChat().then((chat) => {
      if (chat && chat.name && !groupIdCache.has(chat.name.toLowerCase())) {
        groupIdCache.set(chat.name.toLowerCase(), rid);
        console.log(`[bridge] group learned: "${chat.name}" → ${rid}`);
      }
    }).catch(() => {
      if (!groupIdCache.has(rid)) console.log(`[bridge] group id seen: ${rid}`);
    });
  } catch (_e) { /* learning is best-effort */ }
}
client.on('message', learnGroup);
client.on('message_create', learnGroup);
client.on('auth_failure', (m) => { ready = false; state = 'auth_failure'; console.error('[bridge] AUTH FAILURE:', m); });
client.on('disconnected', (reason) => {
  ready = false; state = 'disconnected';
  groupIdCache.clear();
  console.error(`[bridge] disconnected (${reason}) — reinitializing in ${reconnectDelay / 1000}s`);
  setTimeout(() => {
    client.initialize().catch((e) => console.error('[bridge] reinit failed:', e.message));
    reconnectDelay = Math.min(reconnectDelay * 2, 60000);
  }, reconnectDelay);
});

async function resolveGroup(name) {
  // 1. Raw group id given directly (WHATSAPP_GROUP_NAME=1234567890@g.us)
  if (name.endsWith('@g.us')) return name;
  const key = name.toLowerCase();
  // 2. Learned from message traffic (see learnGroup) or a previous lookup
  if (groupIdCache.has(key)) return groupIdCache.get(key);
  // 3. Last resort: getChats() — known to break on WhatsApp Web updates
  let chats;
  try {
    chats = await client.getChats();
  } catch (e) {
    throw new Error(
      `getChats() failed (${errStr(e)}) — this whatsapp-web.js version is out of ` +
      'sync with WhatsApp Web. Fix: (a) run "npm install" to upgrade the library, ' +
      `or (b) send any message in the "${name}" group from your phone — the bridge ` +
      'learns the group id from incoming traffic and this lookup is never needed, ' +
      'or (c) set WHATSAPP_GROUP_NAME to the raw id printed in the bridge log (…@g.us).');
  }
  const group = chats.find((c) => c.isGroup && c.name.toLowerCase() === key);
  if (!group) throw new Error(`group "${name}" not found in this account's chats`);
  groupIdCache.set(key, group.id._serialized);
  return group.id._serialized;
}

function errStr(e) {
  return (e && (e.message || e.toString())) || 'unknown';
}

const app = express();
app.use(express.json({ limit: '64kb' }));

app.get('/health', (_req, res) =>
  res.json({ ready, state, group_cached: groupIdCache.size > 0 }));

// Debug helper: list every group this account can post to (exact names —
// WHATSAPP_GROUP_NAME must match one of these, case-insensitive).
app.get('/groups', async (_req, res) => {
  if (!ready) return res.status(503).json({ ok: false, error: `not ready (${state})` });
  const learned = Object.fromEntries(groupIdCache);
  try {
    const chats = await client.getChats();
    res.json({ ok: true,
               groups: chats.filter((c) => c.isGroup).map((c) => c.name),
               learned });
  } catch (e) {
    // getChats broken (library vs WhatsApp Web drift) — still useful: return
    // everything learned from live traffic + the fix instructions.
    res.json({ ok: true, groups: [], learned,
               note: `getChats() failed (${errStr(e)}). Send any message in your ` +
                     'ops group from your phone — it will appear under "learned" ' +
                     'within seconds, and /send will work by name or by that id.' });
  }
});

app.post('/send', async (req, res) => {
  const { group, message } = req.body || {};
  if (!group || !message)
    return res.status(400).json({ ok: false, error: 'group and message required' });
  if (!ready)
    return res.status(503).json({ ok: false, error: `bridge not ready (${state})` });
  try {
    const chatId = await resolveGroup(group);
    const sent = await client.sendMessage(chatId, message);
    console.log(`[bridge] sent to "${group}" (${message.length} chars)`);
    res.json({ ok: true, id: sent.id ? sent.id._serialized : '' });
  } catch (e) {
    console.error('[bridge] send failed:', errStr(e));
    groupIdCache.delete(group.toLowerCase());   // stale id? re-resolve next time
    res.status(500).json({ ok: false, error: errStr(e) });
  }
});

app.listen(PORT, '127.0.0.1', () =>
  console.log(`[bridge] HTTP on 127.0.0.1:${PORT} — waiting for WhatsApp…`));
client.initialize().catch((e) => console.error('[bridge] init failed:', e.message));
