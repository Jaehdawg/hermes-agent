import { DatabaseSync } from 'node:sqlite';
import path from 'node:path';
import { mkdirSync } from 'node:fs';

/**
 * Durable, non-destructive inbox for native WhatsApp events. The WhatsApp
 * protocol adapter records an event before it is exposed to the bridge. A
 * consumer receives a lease and must explicitly acknowledge after delivery.
 */
export class NativeEventLedger {
  constructor(dbPath) {
    mkdirSync(path.dirname(dbPath), { recursive: true, mode: 0o700 });
    this.db = new DatabaseSync(dbPath);
    this.db.exec(`
      PRAGMA journal_mode = WAL;
      PRAGMA synchronous = FULL;
      CREATE TABLE IF NOT EXISTS native_events (
        message_id TEXT PRIMARY KEY,
        chat_id TEXT NOT NULL,
        sender TEXT NOT NULL,
        text TEXT NOT NULL,
        event_timestamp TEXT NOT NULL,
        received_at TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','leased','acknowledged')),
        lease_owner TEXT,
        lease_until TEXT,
        attempts INTEGER NOT NULL DEFAULT 0,
        acknowledged_at TEXT
      );
      CREATE INDEX IF NOT EXISTS idx_native_events_pending
        ON native_events(state, received_at);
    `);
    this.insert = this.db.prepare(`INSERT OR IGNORE INTO native_events
      (message_id, chat_id, sender, text, event_timestamp, received_at)
      VALUES (?, ?, ?, ?, ?, ?)`);
    this.pendingQuery = this.db.prepare(`SELECT * FROM native_events
      WHERE state = 'pending' ORDER BY received_at ASC LIMIT ?`);
    this.leaseQuery = this.db.prepare(`UPDATE native_events
      SET state = 'leased', lease_owner = ?, lease_until = ?, attempts = attempts + 1
      WHERE message_id = ? AND state = 'pending'`);
    this.ackQuery = this.db.prepare(`UPDATE native_events
      SET state = 'acknowledged', acknowledged_at = ?, lease_owner = NULL, lease_until = NULL
      WHERE message_id = ? AND state = 'leased' AND lease_owner = ?`);
    this.releaseQuery = this.db.prepare(`UPDATE native_events
      SET state = 'pending', lease_owner = NULL, lease_until = NULL
      WHERE state = 'leased' AND lease_until <= ?`);
  }

  record(event, now = new Date().toISOString()) {
    if (!event?.messageId || !event?.chatId || !event?.sender || !event?.text) {
      throw new Error('native event requires messageId, chatId, sender, and text');
    }
    const result = this.insert.run(
      event.messageId, event.chatId, event.sender, event.text,
      event.timestamp || now, now,
    );
    return { inserted: result.changes === 1 };
  }

  pending(limit = 50) {
    return this.pendingQuery.all(Math.max(1, Math.min(Number(limit) || 50, 100))).map(row => ({
      messageId: row.message_id,
      chatId: row.chat_id,
      sender: row.sender,
      text: row.text,
      timestamp: row.event_timestamp,
      receivedAt: row.received_at,
      state: row.state,
      leaseOwner: row.lease_owner,
      leaseUntil: row.lease_until,
      attempts: row.attempts,
    }));
  }

  lease(messageId, owner, leaseMs = 30000, now = new Date()) {
    if (!messageId || !owner) throw new Error('lease requires messageId and owner');
    const until = new Date(now.getTime() + leaseMs).toISOString();
    const result = this.leaseQuery.run(owner, until, messageId);
    return { ok: result.changes === 1, leaseUntil: until };
  }

  ack(messageId, owner, now = new Date().toISOString()) {
    return this.ackQuery.run(now, messageId, owner).changes === 1;
  }

  releaseExpired(now = new Date().toISOString()) {
    return this.releaseQuery.run(now).changes;
  }

  close() { this.db.close(); }
}
