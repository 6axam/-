# Anya 2.0 — журнал прогресса

## 2026-10-15 — Stage 1: полный аудит

**Статус:** завершён.

**Проверено:** актуальный working tree, schema migrations, `emotions`,
`conversation`, `initiative`, `memory`, `episodic_memory`, `self_life`,
`presence`, `daily_life`, `telegram`, `actions`, `llm`, `observability`,
`monitor`, prompts, configuration и test suite.

**Результат:** создана [архитектурная карта](ANYA_2_PLAN.md). Подтверждены
раздельные per-chat affective/relationship/presence states, глобальная
автобиография как частично готовая основа и in-memory-only action queue.

**Рабочее дерево:** уже содержало незакоммиченные изменения в TUI, monitor,
observability, actor-temporal consistency и документации. Они не изменялись в
рамках аудита. Неотслеживаемый `grass` также оставлен нетронутым.

**Следующая задача:** Stage 2 — ввести backwards-compatible shared world/contact
schema, conservative defaults для новых контактов и идемпотентную миграцию без
переключения production path до проверок.

## 2026-10-15 — Stage 2: shared global state и contact profiles

**Статус:** завершён как совместимый foundation; Stage 3 перенесёт dynamics
отношений и coupled эмоций на новый слой.

**Изменено:**

- Добавлена migration с `anya_world_state`, `anya_contacts`,
  `anya_conversation_contacts` и `anya_2_migrations`.
- `SharedStateManager` создаёт ровно один global mind и мигрирует существующие
  отношения по наиболее свежему known chat каждого Telegram user без усреднения
  разных людей. Повторный запуск безопасен; старые таблицы не изменяются.
- Новые контакты получают консервативные values (`love_strength=0`,
  `bond_strength=.05`), а не legacy high-affection defaults.
- Runtime вызывает bootstrap при старте, синхронизирует contact на inbound turn,
  добавляет shared mind/current contact в prompt и зеркалит только broad global
  effect accepted appraisal. Это не добавляет LLM calls и пока не изменяет
  legacy relationship trajectory дважды.
- Добавлен feature flag `ANYA_2_SHARED_STATE_ENABLED` (default `true`); false
  полностью выключает bridge без удаления данных.

**Тесты:** targeted 20 passed in 1.00 s; full 335 passed in 10.62 s;
`python -m compileall -q app` и `git diff --check` прошли.

**Следующая задача:** Stage 3 — сформировать explicit coupling между global mind
и contact affect, перенести безопасную relationship evolution на `ContactProfile`
и добавить seeded long-run simulations без автоматической любви.

## 2026-10-15 — Stage 3: эмоциональная связь global/contact

**Статус:** завершён.

**Изменено:**

- Добавлен чистый `emotions.coupling`: individual half-life для раздражения,
  обиды, resentment, safety и willingness-to-talk; его легко воспроизводимо
  тестировать без LLM и без SQLite.
- `SharedStateManager` теперь продвигает global mind по реальному elapsed time,
  а не по числу reads/frames; каждый канал имеет собственную time constant.
- Appraisal меняет global mood один раз и меняет affect/relationship только
  текущего Telegram contact. Негатив к одному человеку не переносится в его
  relationship profile к другому.
- `AffectiveEngine` получил внешний relationship-bond compatibility seam.
  В включённом Anya 2.0 runtime legacy `relationship_bonds` больше не пишет
  второй раз тот же turn; `ContactProfile` — источник relationship context,
  а legacy state остаётся сохранённым для rollback/read compatibility.
- Repair даёт небольшое снижение rupture и рост trust без instant reset; new
  contacts по-прежнему начинаются без любви.

**Тесты:** targeted 31 passed in 0.83 s; full 338 passed in 9.20 s;
`compileall` и `git diff --check` прошли.

**Следующая задача:** Stage 4 — append-only event journal и сериализованный
event processor: actor, causation, idempotency key, transition/result records.

## 2026-10-15 — Stage 4: event journal bridge

**Статус:** завершён для live conversation path. Более широкие event sources
(presence, schedule, action confirmation) будут добавляться вместе с их stages.

**Изменено:**

- Добавлены `EventEnvelope`, append-only `EventJournal`, serialized
  `EventProcessor`, `anya_events` и `anya_event_transitions`.
- Envelope сохраняет schema version, UTC time, source, actor, target, scope,
  payload, correlation, causation и unique idempotency key.
- В реальном пути reply journal получает отдельные records для incoming turn,
  accepted affective transition и chosen decision. Событие не хранит plaintext
  сообщения: оно ссылается на existing turn/message, сохраняя privacy boundary.
- Ошибка journal не блокирует reply; duplicate ingestion восстанавливает
  исходный record, а не применяет transition повторно.

**Тесты:** targeted 12 passed in 0.44 s; full 340 passed in 9.17 s;
`compileall` и `git diff --check` прошли.

**Следующая задача:** Stage 5 — decisions/intentions с explicit no-op/deferral,
policy gates и совместимой интеграцией с существующим `ActionQueue`.
