# Telegram MTProto — журнал

## 2026-10-15 — Stage 1 audit / Stage 2–3 offline foundation

**Status:** offline foundation complete; awaiting owner-only local login. Bot API
remains the active production transport.

**Verified path:** aiogram update → SQLite idempotency/message persistence →
buffer/read scheduler → ConversationManager/LLM → ActionQueue → aiogram executor.
The normalizer and MTProto adapter must feed this path; no second conversation
brain is permitted.

**Implemented:** transport-neutral inbound DTO/capability contract; lazy
Telethon 1.45+ client; secure session directory/file handling; MTProto config;
safe `login`, `status` and explicitly confirmed Saved Messages `smoke-test`
commands. Unknown contacts, groups and autonomous outbound remain disabled by
configuration. The offline test suite uses a fake client and makes no network
calls.

The strict shared `Settings` model now accepts the MTProto keys, so adding them
to `.env` does not break the existing Bot API startup merely as a side effect.

**Verification:** targeted 8 passed in 0.34 s plus 4 config/transport passed in
0.21 s; full 343 passed in 8.21 s;
`compileall` and `git diff --check` passed.

**UX fix:** cancelling interactive login with `Ctrl+C` now prints a short
cancelled message rather than an asyncio traceback. This does not affect
authorization or the local session.

**Delivery diagnostics:** login now prints the safe delivery channel reported
by Telegram and supports `login --force-sms` where Telegram permits SMS.

**QR authorization:** `login-qr` renders a local terminal QR, waits for a scan
from an already authorized official Telegram client and refreshes expired codes.
It neither asks for nor prints the phone number, a login code, password or the
QR URL/token.

**Live private-text bridge:** when `TELEGRAM_TRANSPORT=mtproto`, the main
runtime uses the authenticated user account instead of Bot API polling. It
admits only incoming private text from `TELEGRAM_ALLOWLIST`, calls the existing
debounce/read/conversation pipeline, sends text replies and read receipts via
Telethon, and namespaces MTProto chats as negative IDs to prevent collision
with Bot API records. Groups, media, stickers, unknown contacts, initiatives
and autonomous outbound remain disabled in this first live slice.

**Blocked on owner action:** local authorization requires a phone login code and
possibly 2FA. No code, number, API hash or session should be shared in Codex.
After the owner completes login, the next permitted step is `status`, then an
explicitly confirmed Saved Messages smoke test.

## 2026-10-15 — Delivery recovery, connection lifecycle and bounded media intake

- Split a durable external dispatch into `claimed` (not yet entered executor)
  and an executor-boundary state. On startup, an interrupted external call is
  stored as `failed` plus `recovery_status=unknown`; it is deliberately not
  retried. A never-started claim can be re-claimed with a compare-and-swap, so
  competing workers cannot both send it.
- Serialized MTProto `connect()` calls, reuses an active client, and uses a
  local session lock to reject a second process opening the same session.
- Private inbound intake now normalizes photo, voice, audio, video, GIF and
  document metadata as well as text/stickers. It does not download, execute or
  persist arbitrary binaries; captions or clear markers are the fallback.
- Read watermark confirmation now performs at most 12 fast retries per window,
  then logs a diagnostic and switches to a 60-second retry. It never treats an
  unconfirmed read as successful.

| Функция | Реализована | Offline-проверка | Live-проверка | Ограничение |
| --- | --- | --- | --- | --- |
| Private text/sticker/media-metadata intake | Да | Да | Нет | Медиа не скачиваются/не транскрибируются автоматически |
| Text, reaction, sticker, image, voice output | Да | Да | Нет | Edit/delete и альбомы не заявлены capability contract |
| Read watermark and outbox-read telemetry | Да | Да | Нет | Нужна авторизация владельца для live проверки |
| Crash-safe action dispatch | Да | Да | Нет | Неопределённый send требует ручного/будущего reconciliation |
| Bot API fallback | Да | Да (регрессии) | Не выполнялась в этой задаче | Остаётся отдельным transport |

## 2026-10-15 — Stable identity bridge

MTProto private inbound events now persist a composite identity consisting of
transport, authenticated account, peer type and peer ID before they enter the
legacy local-chat pipeline. This preserves current conversation history while
preparing a user to appear as one contact across different permitted contexts.
Topics and group participation remain disabled pending their explicit policy
adapter and owner-configured consent.
# 2026-10-19 — Runtime audit fixes

- Receipt confirmation is now race-safe: an asynchronous Telethon
  `MessageRead` update can promote a pending receipt, and a delayed callback
  cannot regress it back to `read_ack_waiting`.
- The read callback rechecks its durable claim after the MTProto network round
  trip. A newer inbound message therefore cancels the old batch before it can
  enter an LLM decision.
- Voice, audio, video, animation and document events retain compact semantic
  markers in SQLite across debounce/restart; no media binary is downloaded.
- MTProto startup no longer creates or requires a Bot API client/token. Bot
  API credentials remain required in `bot_api` mode only.
- Read-only TUI and Monitor now resolve the same negative logical chat ID as
  the MTProto bridge from a single-entry allowlist.
- ActionQueue no longer tries to start durable dispatches for local
  pause/silence controls, and cancellation records unstarted external actions
  terminally instead of leaving recovery noise.
