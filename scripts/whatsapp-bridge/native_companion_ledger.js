#!/usr/bin/env node
/** Durable boundary around a WhatsApp protocol adapter. */
import express from 'express';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { NativeEventLedger } from './native_event_ledger.js';

const args = process.argv.slice(2);
const arg = (name, fallback) => {
  const index = args.indexOf(`--${name}`);
  return index >= 0 && args[index + 1] ? args[index + 1] : fallback;
};
const port = Number(arg('port', process.env.NATIVE_LEDGER_PORT || 3002));
const dbPath = arg('db', process.env.NATIVE_LEDGER_DB || path.join(process.env.HOME, '.hermes', 'whatsapp-native', 'events.sqlite'));
const adapterBase = process.env.NATIVE_PROTOCOL_BASE_URL || 'http://127.0.0.1:3001';
const pullIntervalMs = Math.max(500, Number(process.env.NATIVE_PROTOCOL_PULL_MS || 2000));
const quietMs = Math.max(3000, Number(process.env.NATIVE_EVENT_QUIET_MS || 15000));
const ledger = new NativeEventLedger(dbPath);
const app = express();
app.use(express.json({ limit: '256kb' }));

function validateEvent(raw) {
  const event = {
    messageId: String(raw?.messageId || ''),
    chatId: String(raw?.chatId || ''),
    sender: String(raw?.sender || raw?.chatId || ''),
    text: String(raw?.text || '').trim(),
    timestamp: String(raw?.timestamp || new Date().toISOString()),
  };
  if (!event.messageId || !event.chatId || !event.sender || !event.text) return null;
  return event;
}

function ingest(raw) {
  const event = validateEvent(raw);
  if (!event) return { ok: false, reason: 'invalid event' };
  return { ok: true, ...ledger.record(event) };
}

app.get('/health', (_req, res) => res.json({ status: 'ok', durableLedger: true, adapterBase }));
app.post('/ingest', (req, res) => {
  const result = ingest(req.body);
  res.status(result.ok ? 202 : 400).json(result);
});
app.get('/events', (req, res) => {
  const owner = String(req.query.owner || 'bridge');
  const leaseMs = Math.min(Math.max(Number(req.query.leaseMs) || 30000, 1000), 120000);
  ledger.releaseExpired();
  const events = [];
  for (const pending of ledger.deliverable(quietMs, new Date(), req.query.limit || 20)) {
    const lease = ledger.lease(pending.messageId, owner, leaseMs);
    if (lease.ok) events.push({ ...pending, leaseUntil: lease.leaseUntil });
  }
  res.json(events);
});
app.post('/events/:messageId/ack', (req, res) => {
  const owner = String(req.body?.owner || 'bridge');
  const acknowledged = ledger.ack(req.params.messageId, owner);
  res.status(acknowledged ? 200 : 409).json({ acknowledged });
});
app.post('/send', async (req, res) => {
  // Only the main bridge may dispatch native peer traffic. Direct callers bypass
  // the bridge's outbound-ID tracker and can reflect as owner messages.
  if (req.get('x-hermes-native-dispatch') !== 'bridge') {
    return res.status(403).json({ error: 'Native outbound dispatch must use the main WhatsApp bridge.' });
  }
  try {
    const response = await fetch(`${adapterBase}/send`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify(req.body), signal: AbortSignal.timeout(30000),
    });
    const body = await response.text();
    res.status(response.status).type(response.headers.get('content-type') || 'application/json').send(body);
  } catch (error) {
    res.status(502).json({ error: `protocol adapter unavailable: ${error.message}` });
  }
});

let pulling = false;
async function pullAdapter() {
  if (pulling) return;
  pulling = true;
  try {
    const response = await fetch(`${adapterBase}/messages`, { signal: AbortSignal.timeout(15000) });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    if (!Array.isArray(payload)) return;
    for (const item of payload) ingest(item);
  } catch (error) {
    // The adapter can be offline while pairing; do not terminate the ledger.
    console.warn(`[native-ledger] adapter pull failed: ${error.message}`);
  } finally { pulling = false; }
}

setInterval(() => { void pullAdapter(); }, pullIntervalMs).unref();
app.listen(port, '127.0.0.1', () => console.log(`[native-ledger] listening on 127.0.0.1:${port}`));
