# Telegram Social Integration — progress

## 2026-10-15 — Stage 1 started

- Inspected the local working tree before changes; it already contains the
  transport-owned delayed-job and MTProto media work, which remains uncommitted.
- Found the read-receipt gap: `send_read_acknowledge()` was treated as the
  confirmation itself, while neither a server inbox watermark nor Telethon
  `MessageRead` updates were persisted.
- Next: add durable receipt-state history, server-watermark verification and
  inbox/outbox update handling before changing group/contact behaviour.

## 2026-10-15 — Stage 1 completed: truthful MTProto read receipts

- Added a transport-identity-scoped status/event journal. Incoming state moves
  through `received`, `scheduled_read`, `read_ack_requested`,
  `read_ack_confirmed`, `decision_pending`, and then the existing decision
  path records `response_scheduled` or `silence`. Successful MTProto output is
  separately recorded as `sent`.
- `send_read_acknowledge()` now receives the resolved `InputPeer` and explicit
  maximum message ID. Its boolean is logged as diagnostic data only; a
  `GetPeerDialogs` inbox watermark or an inbox `MessageRead` update is the
  receipt confirmation. A missing/behind watermark leaves the durable read job
  pending for retry instead of starting a response early.
- Added separate inbox and outbox `MessageRead` handlers. Outbox watermark is
  recipient-read telemetry only and never fabricates an outgoing check mark.
- Added read-only Monitor `F8 Telegram` with the operational chain
  `Получено → прочитано Telegram → ответ запланирован → отправлено` and recent
  protocol transitions.
- Offline verification: targeted 33 passed in 1.50 s; full 362 passed in
  11.53 s; `compileall` and `git diff --check` passed. No live Telegram action
  was performed.

## Next

Stage 2: complete the private-action contract (explicit unsupported-action
fallbacks and remaining edit/delete/media semantics) while preserving the
single ActionQueue and transport isolation.

## 2026-10-16 — Stage 2 incremental: own-message mutations

- Added queue-level `edit` and `delete` actions for messages originally sent
  by Anya. Both Bot API and MTProto use their native Telegram methods; no
  second conversation manager or transport crossover was introduced.
- The manager validates a mutation target against the local, non-deleted
  assistant history before queueing it. Executors validate again immediately
  before the external call, so user messages, another contact's message, and
  an already-deleted message cannot be changed.
- A successful edit updates the local copy. A successful delete tombstones it
  locally and removes it from subsequent conversation context while retaining
  a delivery-audit row. MTProto capabilities now truthfully expose edit and
  delete support.
- Remaining Stage 2 attachment outputs (arbitrary documents, audio, video and
  GIF) are deliberately not placed in the model action schema until they have
  a bounded source and executor; this prevents the model from selecting a
  superficially advertised but unavailable action.

## 2026-10-15 — Reliability follow-up

- `action_dispatches` now distinguishes a queue-only claim from the point at
  which an executor may have called Telegram. Restart reconciliation preserves
  at-most-once delivery: in-flight calls are observable as `unknown`, never
  blindly repeated; pre-executor claims are safely eligible for reconstruction.
- Missing read-watermark confirmation is retried in bounded fast windows and
  then backed off with a warning. The monitor labels the interim state
  `ожидается подтверждение Telegram`; no response is generated ahead of proof.
- Fake-transport coverage includes concurrent connection reuse, media metadata
  intake, restart-during-send, reclaimable queue claim and retry-window timeout.

## 2026-10-15 — Multi-contact safety preparation

- Removed runtime labels that hardcoded the original owner name from reaction,
  sticker, photo and history context. Conversation history now uses the compact
  role marker `USER`, so a second allowed contact cannot be described as the
  first one or consume extra context budget.
- This is preparation only; stable account/peer identity mapping and group
  policy remain separate upcoming stages.

## 2026-10-15 — Stage 3 started: stable transport identities

- Added a backward-compatible registry for `transport + account + peer type +
  peer ID + topic`. Existing `local_chat_id` and its negative MTProto namespace
  remain legacy context handles, not the source of protocol identity.
- A protocol user identity links to the existing conservative contact profile;
  a private chat and group topic are independent conversation contexts. Mutable
  usernames are retained only as display metadata and can change safely.
- MTProto inbound registration now records the resolved account/peer identity
  before handing the event to the same existing `ConversationManager`.
- Offline coverage verifies same numeric peer ID in two accounts, a single
  contact in private and group contexts, username change and invalid identity
  rejection. No group traffic or live Telegram operation was enabled.
