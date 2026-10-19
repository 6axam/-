# Agentic brain — progress

## 2026-10-19 — Stage 0 audit

- The local tree already includes Social Cognition Stages 1–3 foundation:
  consent-gated source events, separate social memory, evidence, beliefs and
  revision history. This work reuses those records instead of replacing them.
- Existing MTProto runtime deliberately accepts only allowlisted private
  incoming messages. It has one transport, one `ConversationManager`, one
  `ActionQueue` and durable dispatch/read tracking; group/channel reading is
  not yet a live capability.
- `InitiativeScheduler` is instantiated for both modes, but `main.py` starts
  it only on the Bot API branch. The agent work must adapt and start bounded
  internal review for MTProto without enabling autonomous outreach.
- The next implementation is a validated registry/executor layer; no live
  source, Telegram send or external web call is enabled by this audit.

## 2026-10-19 — Stage 1 complete: typed tool boundary

- Added `ToolRegistry`, `ToolSpec` and bounded `ToolExecutor`. Each call has
  validated input/output, capability and actor-policy checks, a timeout and a
  declared side-effect level. Unknown, malformed or unauthorized calls fail
  closed.
- Added actual offline core tools: scoped private `memory.search`, personal
  `beliefs.search` and `life.get_state`. They reuse existing managers and do
  not make an LLM/network call.
- External Telegram/web/social operations remain absent from the runtime until
  their adapter, source permission and task execution path are implemented.

**Verification:** targeted 29 passed in 1.13 s; full 393 passed in 10.77 s;
`compileall` and `git diff --check` passed.

**Next:** Stage 2 structured task intent integrated into the existing primary
conversation response schema, without an extra LLM call for normal dialogue.
