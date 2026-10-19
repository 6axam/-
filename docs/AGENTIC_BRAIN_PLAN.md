# Agentic brain — staged plan

## Non-negotiable boundaries

- A tool is an explicitly typed capability, not a free-form shell, HTTP or
  Telegram escape hatch.
- Permission is checked before an adapter is called. Source content stays
  untrusted and must pass the existing social consent policy before it may
  become context, memory or evidence.
- The existing `ConversationManager`, `ActionQueue`, event journal and
  transport-specific durable dispatch remain the only conversation/delivery
  path. The agent layer does not create another one.
- External side effects remain deny-by-default. A user request may authorize a
  bounded read but never silently grants send/join/follow/configuration rights.
- Planning/execution is durable, bounded by step/time/cost budgets, and does
  not claim a result until a tool has returned it.

## Stages

0. Audit current conversation, initiative, MTProto, social and state systems;
   document the actual runtime gaps.
1. Add a typed `ToolRegistry` and `ToolExecutor`, with authorization,
   capability checks, validated inputs/outputs and offline core tools.
2. Add structured task intent to the existing primary LLM response, retaining
   one ordinary turn call for normal dialogue.
3. Add durable bounded task orchestration, cancellation, restart recovery and
   tool observations.
4. Implement a fake-tested Telethon read adapter for explicitly permitted
   sources; keep live group/channel operation disabled until configured.
5. Add web/social adapter contracts, fixtures and honest unavailable/fallback
   results without scraping or invented citations.
6. Connect safe task completion to social experience, intentions and the
   existing initiative scheduler; enable its bounded internal review in both
   transport modes before enabling autonomous outbound messages.
7. Add read-only monitor panels and end-to-end offline scenarios.

## Completion criteria

- Natural requests can be classified, planned and executed through typed tools.
- At least two offline end-to-end chains persist task state and return verified
  tool results without a second conversation manager or synthetic success.
- No tool bypasses allowlists, consent scope, source deletion/revocation or
  durable Telegram dispatch.
- MTProto runs the appropriately adapted internal initiative/review loop.
