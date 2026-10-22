import { strict as assert } from 'node:assert';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

import { NativeEventLedger } from './native_event_ledger.js';

const dir = mkdtempSync(path.join(tmpdir(), 'hermes-native-ledger-'));
const dbPath = path.join(dir, 'events.sqlite');
const ledger = new NativeEventLedger(dbPath);

const event = {
  messageId: 'alberta-message-1',
  chatId: '1807055946647697@bot',
  sender: '1807055946647697@bot',
  text: 'A substantive Alberta reply.',
  timestamp: '2026-10-22T02:00:00.000Z',
};
assert.equal(ledger.record(event).inserted, true, 'first receipt must persist');
assert.equal(ledger.record(event).inserted, false, 'duplicate message IDs must dedupe');

let pending = ledger.pending();
assert.equal(pending.length, 1);
assert.equal(pending[0].messageId, event.messageId);
assert.equal(pending[0].attempts, 0);

const lease = ledger.lease(event.messageId, 'bridge-A');
assert.equal(lease.ok, true);
assert.equal(ledger.pending().length, 0, 'active lease must prevent competing delivery');

// A restart must not lose an unacknowledged event.
const restarted = new NativeEventLedger(dbPath);
assert.equal(restarted.releaseExpired('2099-01-01T00:00:00.000Z'), 1);
pending = restarted.pending();
assert.equal(pending.length, 1);
assert.equal(pending[0].attempts, 1);
assert.equal(restarted.lease(event.messageId, 'bridge-B').ok, true);
assert.equal(restarted.ack(event.messageId, 'bridge-B'), true);
assert.equal(restarted.pending().length, 0, 'acknowledged event must not redeliver');

console.log('✓ durable native event ledger persists, deduplicates, leases, retries, and acknowledges');
