# #89 Telegram decision ingress prototype — impact review

Pinned base source: `11e3c97823da31df27fba1bbab609cc912a5ef75`; this all-profile refactor is developed only in the isolated review worktree. **Not installed or enabled.** No gateway config, running process, Telegram, signer key or Notion provider was touched.

## Scope and behavior

- The adapter's existing PTB raw command/text handlers retain their ordinary-message flow when no callback is registered, but reserved `/notion_decide` commands are always quarantined before model intake (also after adapter replacement). The gateway's adapter wiring invokes a **default-off process-local bootstrap** for primary and named-profile bots at startup, hot-serve, and reconnect. An owner-controlled `gateway.notion_decision_service` config block registers the Unix client on startup; a trusted integrator may instead call `runner.configure_notion_decision_ingress(async_callback, bindings=[DecisionBinding(bot_profile="default", profile="default", sender_id=OWNER_ID, chat_id=OWNER_ID), ...])`. Every receiving bot needs an explicit private owner/target binding. Cross-profile targets additionally require an exact bot-scoped `profile_routes` entry with the same user and chat IDs. No sender or service is shipped. `runner.configure_notion_decision_ingress()` removes registrations from live adapters immediately; future replacement adapters remain disabled. Already-running callback effects cannot be recalled. Direct adapter registration remains a low-level testing seam, not a hard access boundary.
- Once registered, only `/notion_decide approve|deny <proposal_id> <nonce>` with bounded URL-safe opaque tokens is reserved. The adapter consumes **all** `/notion_decide...` attempts (including invalid ones) without routing them to the model. Other commands (`/approve`, `/deny`) and ordinary text retain their existing flow. The command is intentionally not registered in BotFather's menu.
- A decision callback receives an immutable `TelegramNotionDecision` with action, opaque proposal/nonce, exact positive raw PTB user ID, private chat ID, message ID, update ID and gateway-resolved profile. Raw `update.message` must be the effective message; edit/channel-post variants, forwarding, replies/quotes, topic, bot, sender-chat, business, wrong sender/chat and malformed commands are rejected before callback. In multiplex mode the receiving bot must be registered under one served profile, the target must belong to the gateway's reserved profile homes, and the route must be unique at its highest specificity and agree with the canonical gateway resolver. Routes are scoped to the receiving bot (`bot_profile`); with no route, its own profile wins (including the primary bot's reserved profile for `_owner_profile=None`). An absent/unknown/denying gateway authorization check blocks the decision, as does the existing adapter prefilter. Failure of callback is swallowed for the model path, not retried or treated as approval. No command/nonce is logged.

## Trust limits / gates before activation

### Owner-controlled production callback (still disabled by default)

The unified gateway loads `gateway.notion_decision_service` from its launch profile's `config.yaml` at process startup (not from message/model metadata). Example, with IDs and UIDs replaced by the operator:

```yaml
gateway:
  notion_decision_service:
    enabled: true
    socket_path: /absolute/private/decisions.sock
    model_uid: 1000
    service_uid: 1001
    unsafe_same_uid_best_effort: true  # only if owner accepts WSL/model-root risk
    bindings:
      - bot_profile: coder
        profile: coder
        sender_id: 111111111
        chat_id: 111111111
```

One binding is required per receiving bot. For cross-profile targets, also configure an exact bot-scoped `profile_routes` entry for Telegram, user and private chat. The client sends the existing version-2 framed raw PTB observation to the Unix decision service; it does **not** create that service or send a card/provider effect. The service still must independently validate the frozen proposal, genuine delivered-card receipt, replay, expiry, target and peer credentials. Strict mode omits `unsafe_same_uid_best_effort` (or sets it to `false`) and requires three distinct gateway/model/service UIDs. The explicit boolean `true` bypasses only that distinct-UID precondition; socket ownership/path and peer UID checks remain. With same-UID or root-capable model access this is a **best-effort operational gate, not a security boundary**; the model could forge socket requests, change local configuration, or bypass the gate. Startup logs an `UNSAFE` warning. A malformed enabled block fails closed. Remove the block or set `enabled: false` and restart the gateway to disable at boot; an in-process operator can call `configure_notion_decision_ingress()` to revoke immediately, including captured callbacks and future reconnects. No gateway config or running service is changed in this review.

1. This is an *ingress filter*, not cryptographic authentication of an arbitrary Python `Update` object. Only PTB's gateway-owned inbound handler can confer transport trust; a same-UID model terminal can import/call Python code and a gateway callback is not a hard privilege boundary by itself. Review the PTB polling/webhook admission path, route identity, profile isolation and any direct invocation surface before enabling this in a real process. No model-facing approval tool is added in this patch.
2. Build a separately privileged decision verifier with narrow IPC/key custody inaccessible to the model and a durable single-use `(chat_id, message_id)` and nonce ledger. Bind proposal, exact frozen rendered payload, source revision, target Notion page, expiry, identity and decision; authenticate the service call, reject replay/expiry/changed digest, and audit. This prototype intentionally does not implement a signer, replay persistence or an effect sender.
3. Before a Notion effect, independently authorize exact owner identity/profile/target and retention, check objections/revocations and source revision, reserve one effect, verify destination mapping and reconcile uncertain provider receipts. Direct Maton write credentials under the model's Unix identity defeat hard separation; the callback alone cannot grant authorization.
4. Review all ingress/command collisions, transport failures and rollback against a staging gateway with fake payloads before an explicit owner decision for any deployment. No production activation, restart, external message or provider call is authorized by this prototype.

## Tests

Focused tests cover disabled quarantine, raw PTB acceptance and rejection, all-profile dedicated-bot routing with the live no-routes multiplex shape, shared-bot target routing, bot-scoped route exclusion, equal-rank ambiguity, unserved target, stale/duplicate bot registration, canonical resolver disagreement, authorization unknown/deny, and callback failures. Bootstrap tests cover default-off shared wiring, explicit default/named bindings, exact cross-profile bot/user/chat routes, runtime disable, captured-callback revocation and replacement. PTB must be pre-imported before pytest starts because `tests/gateway/conftest.py` otherwise installs a stub. Broader auth, batching, and group-gating tests run with the isolated-home guard intact.

The worktree's `.git` points into the installed checkout's `.git/worktrees/` directory. Runner-importing tests trigger `hermes_bootstrap`'s interrupted-update probe on that metadata; `tests/home_io_guard.py` correctly blocks it. For isolated testing, preload `hermes_cli._early_recovery.restore_interrupted_pull = lambda root: False` *in the test process only*, before `pytest.main`, and unset inherited `HERMES_HOME`. No production guard is disabled. The scratch test venv's newer pytest-asyncio emits its own unset-loop-scope deprecation warning.
