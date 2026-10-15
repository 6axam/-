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
