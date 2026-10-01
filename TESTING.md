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

## Maintenance rule

After a code change, record the actual targeted and full `pytest` results here when they are run. Add a new row instead of editing historical measurements. Include only non-sensitive commands and aggregate outcomes; never put API keys, user messages or database contents in this file.
