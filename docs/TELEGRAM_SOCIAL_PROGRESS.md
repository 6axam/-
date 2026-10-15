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

## 2026-10-15 — Multi-contact safety preparation

- Removed runtime labels that hardcoded the original owner name from reaction,
  sticker, photo and history context. Conversation history now uses the compact
  role marker `USER`, so a second allowed contact cannot be described as the
  first one or consume extra context budget.
- This is preparation only; stable account/peer identity mapping and group
  policy remain separate upcoming stages.
