# Alberta durable event ledger

- Adds a source-controlled SQLite event ledger for native Alberta/Muse inbound events.
- Events are deduplicated, leased, retried after expiry, and acknowledged only after a successful Telegram relay.
- Adds a loopback-only ledger sidecar service template on port 3002.
- Keeps the legacy native protocol adapter stopped until a clean pair and live end-to-end validation are completed.
