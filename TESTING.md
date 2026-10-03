# Testing record

Этот файл — короткая карта тестов и журнал фактических прогонов. Это не заменяет CI: времена зависят от компьютера, кэшей и нагрузки.

## How to run

```bash
./.venv/bin/python -m pytest -q
./.venv/bin/python -m compileall app
git diff --check
```

Для точечной проверки запускай нужные файлы, например:

```bash
./.venv/bin/python -m pytest tests/test_lifecycle_initiative.py tests/test_initiative_context.py -q
```

## Test map

| Area | Test files | What is checked |
| --- | --- | --- |
| Database and security | `test_database.py`, `test_security.py`, `test_context_chat_scope.py` | SQLite migrations, idempotency, owner scope and strict chat isolation. |
| Conversation flow | `test_conversation.py`, `test_buffer.py`, `test_delayed_cancellation.py`, `test_read_scheduler.py`, `test_message_cadence.py`, `test_typing_presence.py` | debounce, read/reply delays, cancellation races, generation protection and typing. |
| Context and prompts | `test_context_budget.py`, `test_character_prompt.py`, `test_response_prompt.py`, `test_user_profile_prompt.py` | token budgets, prompt contracts and compact context composition. |
| LLM contracts | `test_llm.py`, `test_offline_integration.py` | OpenAI-compatible requests, native structured output, validation/repair and fake end-to-end flow. |
| Character, memory and life | `test_character_system.py`, `test_memory_v1.py`, `test_self_life.py`, `test_lifecycle_initiative.py`, `test_initiative_context.py` | emotional/lifecycle persistence, Russian memory matching, Anya's autobiography and initiative safety. |
| Daily presence and timing | `test_time_availability.py`, `test_presence_replies.py` | sleep/wake state, college rhythm, events and response timing. |
| Telegram actions | `test_actions.py`, `test_reactions.py`, `test_emoji_filter.py`, `test_message_splitter.py` | sequential delivery, reactions, emoji policy and human-like message splitting. |
| Media, stickers and images | `test_vision_semantic.py`, `test_semantic_production.py`, `test_image_generation.py`, `test_openrouter_images.py` | offline media/sticker pipeline, semantic retrieval and image generation/provider payloads. |

## Recent runs

| Date | Command | Result | Pytest duration | Shell elapsed | Notes |
| --- | --- | --- | ---: | ---: | --- |
| 2026-10-01 | `./.venv/bin/python -m pytest -q` | 159 passed | 7.43 s | 8.74 s | Full offline suite after Initiative v2. |
| 2026-10-01 | `./.venv/bin/python -m pytest tests/test_context_budget.py -q` | 7 passed | 0.39 s | — | Action-tendency settings reach the primary conversation prompt. |
| 2026-10-01 | `./.venv/bin/python -m pytest -q` | 160 passed | 14.17 s | 17.57 s | Full suite after action tendencies and testing record. |
| 2026-10-01 | `./.venv/bin/python -m pytest tests/test_image_generation.py -q` | 8 passed | 2.88 s | — | Pose, micro-action, framing and anti-pattern regression checks. |
| 2026-10-01 | `./.venv/bin/python -m pytest -q` | 161 passed | 5.70 s | 6.83 s | Full suite after image-prompt natural-pose update. |
| 2026-10-01 | `./.venv/bin/python -m pytest tests/test_presence_replies.py tests/test_lifecycle_initiative.py tests/test_context_budget.py -q` | 29 passed | 3.05 s | — | Bedtime-window, initiative gate and compact context checks. |
| 2026-10-01 | `./.venv/bin/python -m pytest -q` | 164 passed | 6.25 s | 7.34 s | Full suite after Bedtime ritual. |
| 2026-10-01 | `./.venv/bin/python -m pytest tests/test_lifecycle_initiative.py tests/test_presence_replies.py -q` | 23 passed | 4.23 s | — | Bedtime timing and enqueue-failure regression checks. |
| 2026-10-01 | `./.venv/bin/python -m pytest -q` | 166 passed | 6.17 s | 7.37 s | Full suite after bedtime timing/persistence fixes. |
| 2026-10-01 | `./.venv/bin/python -m pytest tests/test_voice.py tests/test_llm.py tests/test_response_prompt.py -q` | 21 passed | 4.18 s | — | Voice schema, provider/executor, config and compact prompt checks. |
| 2026-10-01 | `./.venv/bin/python -m pytest -q` | 172 passed | 6.48 s | 7.65 s | Full suite after outgoing voice-message scaffold. |
| 2026-10-01 | `./.venv/bin/python -m pytest tests/test_voice.py tests/test_context_budget.py tests/test_llm.py -q` | 28 passed | 4.36 s | — | BytePlus request/response, voice availability and config contract checks. |
| 2026-10-01 | `./.venv/bin/python -m pytest -q` | 174 passed | 6.43 s | 7.61 s | Full suite after Seed Audio provider integration. |
| 2026-10-01 | `./.venv/bin/python -m pytest tests/test_voice.py tests/test_response_prompt.py -q` | 10 passed | 3.84 s | — | Voice capability exposure and compact-prompt contract. |
| 2026-10-01 | `./.venv/bin/python -m pytest -q` | 174 passed | 6.02 s | 7.18 s | Full suite after direct voice-request policy fix. |
| 2026-10-01 | `./.venv/bin/python -m pytest tests/test_voice.py tests/test_llm.py tests/test_response_prompt.py -q` | 27 passed | 3.25 s | — | OpenRouter Seed payload, key-resolution, MP3 executor and capability contracts. |
| 2026-10-01 | `./.venv/bin/python -m pytest -q` | 178 passed | 5.44 s | — | Full suite after replacing the direct BytePlus voice path with OpenRouter Seed Audio. |
| 2026-10-03 | `./.venv/bin/python -m pytest tests/test_presence_replies.py tests/test_llm.py tests/test_response_prompt.py -q` | 29 passed | 4.69 s | — | One-time owner-requested bedtime delay, schema and compact-prompt checks. |
| 2026-10-03 | `./.venv/bin/python -m pytest -q` | 181 passed | 8.49 s | — | Full suite after one-time later-bedtime support. |
| 2026-10-03 | `./.venv/bin/python -m pytest tests/test_image_generation.py tests/test_openrouter_images.py -q` | 10 passed | 4.35 s | — | Reference-identity prompt and OpenRouter image-reference checks. |
| 2026-10-03 | `./.venv/bin/python -m pytest -q` | 181 passed | 8.38 s | — | Full suite after canonical appearance-prompt update. |
| 2026-10-03 | `./.venv/bin/python -m pytest tests/test_image_generation.py tests/test_openrouter_images.py -q` | 10 passed | 2.87 s | — | Flexible reference-identity and image provider contract checks. |
| 2026-10-03 | `./.venv/bin/python -m pytest -q` | 181 passed | 6.03 s | — | Full suite after neutral-expression reference guidance. |
| 2026-10-03 | `./.venv/bin/python -m pytest tests/test_emotions.py -q` | 4 passed | 0.12 s | — | Per-chat baseline, bounded sparse deltas, deterministic decay and fatigue checks. |
| 2026-10-03 | `./.venv/bin/python -m pytest -q` | 185 passed | 8.88 s | — | Full suite after persistent emotional-engine foundation. |
| 2026-10-03 | `./.venv/bin/python -m pytest tests/test_emotions.py tests/test_emotion_lifecycle.py tests/test_llm.py tests/test_context_budget.py tests/test_character_system.py -q` | 35 passed | 1.34 s | — | Luna emotion-delta schema, accepted-response lifecycle and compact context checks. |
| 2026-10-03 | `./.venv/bin/python -m pytest -q` | 188 passed | 8.84 s | — | Full suite after Luna emotional-continuity integration. |
| 2026-10-03 | `./.venv/bin/python -m pytest tests/test_time_availability.py tests/test_lifecycle_initiative.py tests/test_presence_replies.py tests/test_delayed_cancellation.py -q` | 45 passed | 5.06 s | — | Bounded emotional pacing and spontaneous-initiative behaviour under existing safety rails. |
| 2026-10-03 | `./.venv/bin/python -m pytest -q` | 190 passed | 8.99 s | — | Full suite after emotional behaviour modifiers. |
| 2026-10-03 | `./.venv/bin/python -m pytest tests/test_episodic_memory.py tests/test_emotions.py tests/test_emotion_lifecycle.py tests/test_context_budget.py -q` | 16 passed | 0.63 s | — | Episodic snapshot, open-loop, safe resolution and supersession checks. |
| 2026-10-03 | `./.venv/bin/python -m pytest -q` | 192 passed | 9.16 s | — | Full suite after episodic continuity memory. |
| 2026-10-03 | `./.venv/bin/python -m pytest tests/test_context_budget.py tests/test_llm.py tests/test_episodic_memory.py tests/test_emotion_lifecycle.py -q` | 27 passed | 1.08 s | — | Context budgets, schema, episodic continuity and lifecycle checks. |
| 2026-10-03 | `./.venv/bin/python -m pytest -q` | 192 passed | 9.10 s | — | Full suite after context expansion and telemetry accounting. |
| 2026-10-03 | `./.venv/bin/python -m pytest tests/test_image_generation.py tests/test_openrouter_images.py -q` | 10 passed | 4.50 s | — | Flexible canonical image-reference guidance and provider contract checks. |
| 2026-10-03 | `./.venv/bin/python -m pytest tests/test_emotions.py tests/test_presence_replies.py tests/test_time_availability.py -q` | 32 passed | 4.96 s | — | Sleep recovery, night wakefulness, presence and timing checks. |
| 2026-10-03 | `./.venv/bin/python -m pytest -q` | 194 passed | 7.77 s | — | Full suite after emotional sleep-recovery fix. |
| 2026-10-03 | `./.venv/bin/python -m pytest tests/test_episodic_memory.py tests/test_emotion_lifecycle.py tests/test_context_budget.py tests/test_llm.py -q` | 28 passed | 1.05 s | — | Episodic relevance, standalone resolution, context and schema checks. |
| 2026-10-03 | `./.venv/bin/python -m pytest -q` | 195 passed | 9.24 s | — | Full suite after episodic retrieval and guidance refinement. |
| 2026-10-03 | `./.venv/bin/python -m pytest tests/test_lifecycle_initiative.py tests/test_episodic_memory.py tests/test_context_budget.py tests/test_emotions.py -q` | 31 passed | 1.01 s | — | Initiative rails, episodic open-loop retrieval, context and emotional continuity checks. |
| 2026-10-03 | `./.venv/bin/python -m pytest -q` | 195 passed | 9.05 s | — | Full suite after open-loop initiative integration. |
| 2026-10-03 | `./.venv/bin/python -m pytest tests/test_affective_chemistry.py tests/test_emotions.py -q` | 7 passed | 0.15 s | — | Persistent affective chemistry baseline, sleep and deterministic evolution. |
| 2026-10-03 | `./.venv/bin/python -m pytest -q` | 196 passed | 6.39 s | — | Full suite after affective chemistry foundation. |
| 2026-10-03 | `./.venv/bin/python -m pytest tests/test_affective_profile.py tests/test_affective_chemistry.py tests/test_emotions.py tests/test_emotion_lifecycle.py -q` | 11 passed | 0.28 s | — | Affective spectrum, behavior profile, chemistry and lifecycle checks. |
| 2026-10-03 | `./.venv/bin/python -m pytest -q` | 198 passed | 6.38 s | — | Full suite after affective emotional spectrum. |
| 2026-10-03 | `./.venv/bin/python -m pytest tests/test_affective_appraisal.py tests/test_affective_chemistry.py tests/test_affective_profile.py tests/test_emotion_lifecycle.py tests/test_llm.py -q` | 22 passed | 0.67 s | — | Appraisal schema, caps, persistence, spectrum and lifecycle checks. |
| 2026-10-03 | `./.venv/bin/python -m pytest -q` | 200 passed | 6.07 s | — | Full suite after Luna affective appraisal integration. |
| 2026-10-03 | `./.venv/bin/python -m pytest tests/test_context_budget.py tests/test_response_prompt.py tests/test_lifecycle_initiative.py tests/test_time_availability.py -q` | 39 passed | 0.61 s | — | Affective context, prompt compactness, initiative and timing checks. |
| 2026-10-03 | `./.venv/bin/python -m pytest -q` | 200 passed | 6.21 s | — | Full suite after affective behavior guidance. |

## Maintenance rule

After a code change, record the actual targeted and full `pytest` results here when they are run. Add a new row instead of editing historical measurements. Include only non-sensitive commands and aggregate outcomes; never put API keys, user messages or database contents in this file.
