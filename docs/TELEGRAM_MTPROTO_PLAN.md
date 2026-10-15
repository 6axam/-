# Telegram MTProto — план

## Audit: текущий путь

`aiogram` handler deduplicates `telegram_updates`, persists `messages`, then
passes an aiogram `Message` into `IncomingBuffer`. `ReadScheduler` creates a
turn, `ConversationManager` builds context and calls the LLM, and the in-memory
`ActionQueue` invokes `TelegramActionExecutor` to send through aiogram Bot API.
Delayed reads/replies, cancellation and typing are attached to this path.

**Integration seam:** normalized inbound DTO → existing persistence/buffer/core;
typed `Action` → transport-aware executor. The core retains `chat_id`, user ID,
turn and generation semantics. MTProto must never create a second manager.

## Phases

1. **Offline foundation (current):** capability DTO, lazy Telethon client,
   configuration, secure session path and CLI; no login or outbound traffic.
2. **Authorization:** owner runs local `login`; no code/password is provided to
   Codex. `status` confirms only redacted identity information.
3. **Safe test:** explicit confirmation sends one message only to Saved Messages.
4. **Adapter:** normalized allowlisted private inbound events feed the existing
   core; outgoing typed actions use the same ActionQueue.
5. **Media/read/typing:** transport-neutral file handling and real receipts.
6. **Reliability/monitor:** outbox reconciliation, flood/reconnect diagnostics,
   read-only monitor page.

## Safety defaults

`bot_api` remains the default transport. MTProto is disabled and dry-run by
default. Unknown contacts, groups and autonomous outbound messaging are denied.
The session is local under `data/mtproto`, ignored by Git and never sent to
observability. Live login, Saved Messages smoke test and any test contact each
require an explicit local command/confirmation.
