# Карта связей кода и отладки

Актуально на 2026-10-09. Это навигационная карта проекта, а не runtime-лог и не источник секретов. Она описывает фактические связи между модулями, очередями и SQLite, чтобы быстрее локализовать баги.

## Быстрый вход

| Что нужно понять или исправить | Начинать с |
| --- | --- |
| Бот не получает или не отвечает на сообщение | `app/main.py` → `app/telegram/handlers.py` → `app/conversation/read_scheduler.py` |
| Аня отвечает странно или не тем тоном | `app/conversation/manager.py` → `app/conversation/context.py` → `app/llm/openai_provider.py` |
| Ответ сформирован, но не пришёл в Telegram | `app/conversation/response_scheduler.py` → `app/actions/queue.py` → `app/telegram/executor.py` |
| Эмоции, отношения, конфликт или repair | `app/emotions/affective.py` → `profile.py` / `expression.py` / `relationship.py` |
| Память не попала в ответ или извлечена нерелевантно | `app/memory/manager.py`, `app/memory/retrieval.py`, `app/episodic_memory.py` |
| Стикеры, фото, image/voice action | `app/stickers/`, `app/media/`, `app/images.py`, `app/voice/` |
| Инициатива не приходит | `app/initiative/scheduler.py` → `app/initiative/context.py` |
| TUI показывает неверное состояние | `app/tui/state.py` → `app/tui/scenes.py` → `app/tui/app.py` |

## Точки входа

| Команда / entry point | Файл | Что запускает |
| --- | --- | --- |
| `python -m app.main` | `app/main.py:main()` | Production Telegram bot, SQLite, background scheduler'ы, LLM provider и polling. |
| `python -m app.tui` | `app/tui/__main__.py:main()` | Read-only Textual observer SQLite. Не запускает второго Telegram bot. |
| `python -m app.tui --preview ...` | `app/tui/__main__.py` + `app/tui/preview.py` | Synthetic visual preview; не читает settings/SQLite/Telegram/LLM. |
| `python -m app.tools.affective_history` | `app/tools/affective_history.py` | Offline просмотр affective history. |
| `python -m app.diagnostics` | `app/diagnostics.py` | Локальная диагностика конфигурации/состояния. |

## Production boot graph

```text
app.main
  ├─ load_settings() ────────────────────────────── app/config.py
  ├─ Database.connect() ─────────────────────────── app/database/db.py
  ├─ Bot + Dispatcher ───────────────────────────── aiogram
  ├─ LLM provider ───────────────────────────────── app/llm/openai_provider.py
  ├─ ConversationManager
  │    ├─ ContextBuilder
  │    ├─ ActionQueue → TelegramActionExecutor
  │    ├─ ResponseScheduler / ReadScheduler
  │    ├─ emotion + affective + relationship engines
  │    └─ memory / episodic memory / self life
  └─ TaskGroup
       ├─ ResponseScheduler.run()
       ├─ ReadScheduler.run()
       ├─ InitiativeScheduler.run()
       ├─ DailyLifeScheduler.run()                 (если включён)
       ├─ sticker/media analysis workers            (если включены)
       └─ Dispatcher.start_polling(bot)
```

`app/main.py` — composition root: именно здесь создаются зависимости и связываются callbacks. При баге, который выглядит как «модуль не работает вообще», сначала проверь, что он создан и передан здесь.

## Главный путь входящего Telegram-сообщения

```text
Telegram update
  → app/telegram/handlers.py
  → Database.claim_update()                         idempotency
  → Database.record_message()
  → ConversationManager.interrupt()                 отмена старого хвоста
  → ConversationLifecycleManager.on_user_message()
  → IncomingBuffer.add()                            debounce
  → flush callback в app/main.py
  → ReadScheduler.schedule()
  → ReadScheduler.run() / process_due()
  → on_messages_read() callback в app/main.py
  → Database.read_batch_for_boundary()
  → ConversationManager.handle_turn()
  → ConversationManager._generate()
  → LLM provider.complete()
  → ResponseScheduler.schedule()
  → ResponseScheduler.run() / process_due()
  → ConversationManager.handle_scheduled()
  → ActionQueue.enqueue_many()
  → TelegramActionExecutor.execute()
  → Telegram API + Database.record_message()
```

### Router и media branches

`app/telegram/handlers.py:make_router()` создаёт три отдельных пути.

| Telegram event | Дополнительный путь |
| --- | --- |
| Text | Сохраняется как `messages.kind='text'`, затем идёт в debounce/read pipeline. |
| Photo | `MediaManager.ingest_photo()` сохраняет локальный media input, затем идёт в тот же pipeline. |
| Sticker | `StickerManager.register_incoming()`; при включённом vision возможны `StickerAnalysisWorker` и fallback frames; затем тот же pipeline. |
| Reaction | Обходит debounce и LLM: сохраняется в `message_reactions`, затем `ConversationManager.observe_reaction()`. |

Security gate находится в `app/telegram/security.py:is_owner()`. Любой handler сначала проверяет owner и `claim_update`; не обходить эти проверки при добавлении нового вида update.

## Генерация ответа и действия

`ConversationManager.handle_turn()` — центральный orchestration слой одного пользовательского turn.

```text
handle_turn
  → presence / lifecycle / delay state
  → _generate
      → ContextBuilder.build_with_breakdown
      → provider.generate / structured response
      → affective appraisal + emotion update
      → relationship update
      → episodic memory update
      → response validation + MessageSplitter
      → ResponseScheduler.schedule
  → handle_scheduled
      → ActionQueue
      → TelegramActionExecutor
```

Основные файлы:

| Файл | Ответственность | Не должен делать |
| --- | --- | --- |
| `app/conversation/manager.py` | Оркестрация turn, отмена generation, lifecycle, post-generation persistence. | Сам отправлять Telegram action мимо очереди. |
| `app/conversation/context.py` | Собирает prompt/context и соблюдает token budgets. | Менять durable state. |
| `app/llm/openai_provider.py` | HTTP вызов OpenAI-compatible provider, schema/repair/retry. | Принимать Telegram update. |
| `app/llm/schemas.py` | Typed LLM contracts. | Содержать business policy. |
| `app/conversation/response_scheduler.py` | Durable scheduled response и restart recovery. | Генерировать LLM response. |
| `app/actions/queue.py` | Последовательное исполнение и cancellation per generation. | Выбирать содержание ответа. |
| `app/telegram/executor.py` | Typing, message/sticker/reaction/image/voice delivery. | Менять prompt/context. |

### Где искать проблему по симптомам

| Симптом | Первые проверки |
| --- | --- |
| Ответ не генерируется | `scheduled_reads`, `ReadScheduler`, логи `handle_turn`, provider timeout/error. |
| Ответ есть в LLM log, но нет в Telegram | `scheduled_responses`, `ResponseScheduler`, `ActionQueue`, `TelegramActionExecutor`. |
| Старый ответ приходит после нового user message | `ConversationManager.interrupt()`, `ActionQueue.cancel_generation()`, `ResponseScheduler.cancel_chat()`. |
| Ответ обрезан / split странный | `MessageSplitter`, `actions.models.Action.validate_payload()`. |
| В логах `ConnectTimeout` | Provider endpoint/network. Это не SQLite/TUI баг; обработчик пропускает недоступную инициативу или response согласно своему fallback. |

## Prompt/context graph

`ContextBuilder.build_with_breakdown()` в `app/conversation/context.py` собирает system prompt и request context.

```text
prompts/*.md
  + recent messages                         messages
  + user memories                           memories
  + episodic memories                       episodic_memories
  + developed personality                   developed_personality
  + emotional + affective state             emotional_states / affective_*
  + relationship context                    relationship_bonds
  + daily life / presence / bedtime state   daily_*
  + sticker/reaction/media context          sticker_* / message_reactions / media
  → LLM request
```

Prompt source files:

| Area | Source |
| --- | --- |
| Character and life background | `prompts/character.md`, `prompts/life_background.md` |
| Response contract | `prompts/response.md` |
| Emotion / episodic policy | `prompts/emotional_state.md`, `prompts/episodic_memory.md` |
| User memory | `prompts/memory.md`, `prompts/user_profile.md` |
| Initiative | `prompts/initiative.md` |
| Media | `prompts/sticker_analyzer.md`, `prompts/photo_style.md`, `prompts/image_custom.md`, `prompts/voice_*.md` |

Для разбора слишком большого prompt смотри `estimated_input_tokens` и `components` в логах `app.llm.openai_provider`. Поле `target_input_tokens` — бюджет, а не гарантия, что provider оборвал запрос.

## Эмоции, affect и отношения

```text
LLM emotional/appraisal output
  → app/emotions/affective.py:AffectiveEngine
  → profile.py                              emotions_json
  → expression.py                           CurrentExpressionIntent
  → relationship.py:RelationshipBondManager relationship_bonds
  → history.py                              append-only affective history
  → ContextBuilder                          guidance for the next/current response
```

| Файл | Что хранит/решает |
| --- | --- |
| `app/emotions/engine.py` + `model.py` | Legacy compact emotional continuity. |
| `app/emotions/affective.py` | Appraisal, chemistry, regulators, affective update orchestration. |
| `app/emotions/profile.py` | Broad emotion profile and behavior channels. |
| `app/emotions/expression.py` | Same-turn `CurrentExpressionIntent`: sharpness, repair, withdrawal, CAPS/profanity drives. |
| `app/emotions/relationship.py` | Bond, trust, security, rupture, love inertia. |
| `app/emotions/history.py` | Append-only transition telemetry. |
| `app/episodic_memory.py` | Affective episode snapshot, relevance and unresolved/open-loop behaviour. |

Важно: любовь/bond специально инерционны; trust/security и rupture могут двигаться заметно быстрее. Не «чинить» это одним общим clamp.

## Память и личная жизнь

```text
LLM memory candidates → MemoryExtractor → MemoryManager.apply → memories
user turn             → MemoryRetrieval.search → ContextBuilder

post-event affect snapshot → EpisodicMemoryManager → episodic_memories
user turn + affect relevance → EpisodicMemoryManager.relevant → ContextBuilder

DailyLifeScheduler / SelfLifeManager → daily_events / anya_life_events
```

`MemoryManager` предназначен для устойчивых user facts. `EpisodicMemoryManager` — для конкретных отношений, обещаний, конфликтов и незакрытых тем. Для проблемы «Аня вспомнила не то» проверяй semantic relevance, affective relevance и token budget, а не только SQL row order.

## Инициатива и daily life

```text
InitiativeScheduler.run_once
  → lifecycle + unread/pending safety checks
  → DailyPresenceManager / daily event state
  → InitiativeContextBuilder.build
  → provider.decide_initiative
  → ConversationManager.enqueue_initiative
  → ResponseScheduler / ActionQueue / executor
```

Связанные файлы:

- `app/initiative/scheduler.py` — eligibility, safety blocks, probability, cancellation.
- `app/initiative/context.py` — ограниченный initiative prompt context.
- `app/daily_life.py` — LLM decision для daily event, если включено.
- `app/presence.py` — сон, колледж, availability, текущие события.
- `app/self_life.py` — долгие personal life events.
- `app/weather.py` — optional weather context.

Если initiative не срабатывает, сначала ищи `initiative_blocked` и его `reason`; это обычно ожидаемый safety gate, а не исключение.

## Media, stickers, images и voice

```text
incoming sticker → StickerManager → sticker_analysis_jobs → StickerAnalysisWorker
incoming photo   → MediaManager → message_media → MediaDescriptionWorker
LLM action       → TelegramActionExecutor → image / voice provider → sent media
```

| Component | Основные данные |
| --- | --- |
| `app/stickers/manager.py`, `retriever.py`, `analyzer.py` | `stickers`, `sticker_sets`, `sticker_semantics`, `sticker_embeddings`, `sticker_usage`, `sticker_analysis_jobs` |
| `app/media/manager.py`, `analyzer.py` | `media`, `message_media`, `media_analysis_jobs`, `media_descriptions` |
| `app/images.py` | Prompt construction, reference image, OpenAI/OpenRouter image payload. |
| `app/voice/openrouter_seed.py` | Voice request and reference audio validation. |
| `app/telegram/executor.py` | Единственная точка фактической отправки Telegram media/action. |

## SQLite: главные таблицы и владельцы

| Таблицы | Основные владельцы |
| --- | --- |
| `users`, `telegram_updates`, `messages`, `conversation_turns` | `Database`, Telegram handlers, ConversationManager. |
| `scheduled_reads`, `scheduled_responses` | ReadScheduler и ResponseScheduler. |
| `conversation_lifecycle` | ConversationLifecycleManager. |
| `memories`, `developed_personality` | MemoryManager, PersonalityManager. |
| `episodic_memories` | EpisodicMemoryManager. |
| `emotional_states`, `affective_states`, `affective_profiles` | EmotionalEngine, AffectiveEngine. |
| `relationship_bonds`, `relationship_state` | RelationshipBondManager and compatibility state. |
| `daily_events`, `daily_presence`, `bedtime_*`, `anya_life_events` | presence, daily life, self life. |
| `stickers`, `sticker_*` | StickerManager and analysis worker. |
| `media`, `message_media`, `media_*` | MediaManager and description worker. |
| `generated_images`, `image_generation_usage` | TelegramActionExecutor / image generation flow. |
| `initiative_history` | InitiativeScheduler. |

Schema evolution lives in `app/database/migrations/`. Do not edit a live database schema ad hoc; add a migration and cover it in `tests/test_database.py`.

## TUI graph

```text
normal TUI
  app/tui/__main__.py
    → ReadOnlyStateReader.poll()             SQLite mode=ro + query_only
    → AnyaSnapshot
    → resolve_scene()
    → AnimationController
    → render_scene()
    → Textual Static widgets

preview TUI
  app/tui/__main__.py --preview
    → PreviewSession.synthetic snapshot
    → AnimationController
    → procedural renderer
    → Textual Static widgets
```

| File | Role |
| --- | --- |
| `app/tui/state.py` | Read-only snapshot acquisition; only normal TUI uses it. |
| `app/tui/scenes.py` | Scene resolution; legacy renderer and hand-off to pixel renderer. |
| `app/tui/pixels.py` | Logical PixelCanvas, compositing, lighting and half-block `▀` renderer. |
| `app/tui/sprite.py` | Composable Anya pose/expression drawing. |
| `app/tui/animation.py` | Local time-based animation, RNG, tweening and transitions. |
| `app/tui/pixel_scenes.py` | Cached static geometry plus procedural scene composition. |
| `app/tui/preview.py` | Synthetic state and preview-only controls. |
| `app/tui/app.py` | Textual shell, polling/preview mode separation and overlays. |

The current procedural pixel path is intentionally first proven on `IDLE_HOME`; other scene kinds may still use the legacy renderer until they are migrated deliberately.

## Tests as a bug map

| Bug area | First tests to run |
| --- | --- |
| Incoming flow / cancellation | `tests/test_conversation.py`, `test_buffer.py`, `test_delayed_cancellation.py`, `test_read_scheduler.py` |
| Prompt size and LLM contract | `tests/test_context_budget.py`, `test_llm.py`, `test_response_prompt.py` |
| Affect / expression / relationship | `tests/test_affective_*.py`, `test_current_expression.py`, `test_emotional_v3_tuning.py`, `test_relationship_*.py` |
| Memory / episodes | `tests/test_memory_v1.py`, `test_episodic_memory.py` |
| Initiative / presence | `tests/test_lifecycle_initiative.py`, `test_initiative_context.py`, `test_time_availability.py` |
| Telegram delivery / actions | `tests/test_actions.py`, `test_message_splitter.py`, `test_reactions.py`, `test_typing_presence.py` |
| Images / stickers / voice | `tests/test_image_generation.py`, `test_vision_semantic.py`, `test_semantic_production.py`, `test_voice.py` |
| TUI | `tests/test_tui*.py` |

Useful commands:

```bash
./.venv/bin/python -m pytest -q tests/test_conversation.py
./.venv/bin/python -m pytest -q tests/test_emotional_v3_tuning.py tests/test_current_expression.py
./.venv/bin/python -m pytest -q tests/test_tui.py tests/test_tui_pixels.py tests/test_tui_preview.py
./.venv/bin/python -m pytest -q
git diff --check
```

## Rules while debugging

- Start at the first durable boundary: Telegram update, DB row, scheduled row, LLM response, queued action, sent Telegram message.
- Do not infer model output from response style; inspect provider metadata/logs where available.
- Keep `chat_id` in every investigation. Most persistent state is chat-scoped.
- For a production incident, inspect first; do not mutate JSONL/SQLite while collecting evidence.
- Avoid `git reset --hard` and direct deletion of runtime data. Make targeted fixes with regression tests.
- `README.md` is user-facing setup documentation. This file is the engineering/debugging index.
