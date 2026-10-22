import { strict as assert } from 'node:assert';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { NativeEventLedger } from './native_event_ledger.js';

const dir = mkdtempSync(path.join(tmpdir(), 'hermes-native-ledger-'));
const ledger = new NativeEventLedger(path.join(dir, 'events.sqlite'));
const event = { messageId: 'alberta-message-1', chatId: '1807055946647697@bot', sender: '1807055946647697@bot', text: 'A substantive Alberta reply.', timestamp: '2026-10-22T02:00:00.000Z' };
assert.equal(ledger.record(event).inserted, true, 'first receipt must persist');
assert.equal(ledger.record(event).inserted, false, 'duplicate message IDs must dedupe');
assert.equal(ledger.pending().length, 1);
const lease = ledger.lease(event.messageId, 'bridge-A');
assert.equal(lease.ok, true);
assert.equal(ledger.pending().length, 0, 'active lease must prevent competing delivery');
const restarted = new NativeEventLedger(path.join(dir, 'events.sqlite'));
assert.equal(restarted.releaseExpired('2099-01-01T00:00:00.000Z'), 1);
assert.equal(restarted.lease(event.messageId, 'bridge-B').ok, true);
assert.equal(restarted.ack(event.messageId, 'bridge-B'), true);
assert.equal(restarted.pending().length, 0, 'acknowledged event must not redeliver');

// A native bot can emit progressively longer prefixes under different IDs.
// Coalesce them, wait for a quiet window, then expose exactly the final body.
const t0 = new Date('2026-10-22T03:00:00.000Z');
const short = { ...event, messageId: 'stream-1', text: '## 1) **VERIFIED** now', timestamp: t0.toISOString() };
const long = { ...short, messageId: 'stream-2', text: '1) *VERIFIED* now with a complete final result.' };
assert.equal(restarted.record(short, t0.toISOString()).inserted, true);
assert.deepEqual(restarted.record(long, new Date(t0.getTime() + 1000).toISOString()), { inserted: false, duplicate: 'progressive' });
assert.equal(restarted.deliverable(12000, new Date(t0.getTime() + 12000)).length, 0, 'quiet window must hold partial stream');
const stable = restarted.deliverable(12000, new Date(t0.getTime() + 14000));
assert.equal(stable.length, 1);
assert.equal(stable[0].text, long.text, 'only the final complete progressive body is delivered');

console.log('✓ durable ledger persists, leases, retries, and coalesces progressive native replies');
