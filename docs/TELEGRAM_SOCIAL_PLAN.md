# Telegram Social Integration — plan

## Invariants

- One `ConversationManager`, `SharedStateManager`, `EventProcessor`,
  `ActionQueue`, `ReadScheduler` and one MTProto transport adapter.
- Telegram adapters only normalize inbound updates, execute permitted actions,
  and report delivery/read state. They do not make social decisions.
- New outbound/social behaviour is disabled by default and must pass an
  allowlist, consent and rate-limit policy before it can reach Telegram.
- A local DB state means only what it says: it never claims that Telegram or a
  recipient has confirmed something unless an MTProto update or server
  watermark confirmed it.

## Staged delivery

1. **Read receipts and private-DM reliability (active).** Persist a compact
   status trail for incoming/outgoing MTProto messages; use the exact cached
   `InputPeer` and a maximum message ID; distinguish acknowledgement requested
   from confirmed server watermark; ingest both inbox and outbox `MessageRead`
   updates; expose the four operational states in the read-only monitor.
2. **Complete private actions.** Make capabilities match executable actions;
   add explicit, observable rejection/fallback for unavailable action types;
   finish reply/edit/delete and transport-neutral media with idempotent
   delivery/reconnect handling.
3. **Multi-contact identity.** Introduce stable Telegram identity to local
   contact/conversation mapping, migrate the temporary private-chat namespace,
   and verify three simultaneous allowlisted contacts cannot leak context.
4. **Groups.** Add disabled-by-default, per-chat `OFF`/`OBSERVE_ONLY`/
   `MENTIONS_ONLY`/`PARTICIPATE` policy, participant consent and strict
   addressability/cooldown controls.
5. **Permitted sources.** Add a registry and cursor/deduplication pipeline for
   RSS/APIs/permitted channel sources. Source content is untrusted input and is
   not sent wholesale to an LLM.
6. **Social decisions and monitor.** Connect permitted source/contact/group
   events to the existing decision/event systems and add read-only Russian
   monitor panels plus local CLI/config for mutating policy.

## Stage 1 acceptance criteria

- `received → scheduled_read → read_ack_requested →
  read_ack_confirmed → decision_pending → response_scheduled|silence → sent`
  is durable and truthful.
- An RPC `False` is diagnostic data, not a synthetic network failure. A
  server-side inbox watermark or an inbox `MessageRead` confirms a read.
- Outbox `MessageRead` tracks recipient-read information separately and never
  manufactures a second check mark.
- Restart, debounce, repeated acknowledgement, multi-contact and read-race
  cases have fake-Telethon/temporary-SQLite coverage. No pytest sends live
  Telegram traffic.
