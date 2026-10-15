# Anya 2.0 — план автономной жизни и разума

Дата начала: 2026-10-15. Этот документ описывает поэтапную миграцию существующего
runtime, а не замену работающего бота. До явного включения каждого флага Bot API
остается действующим транспортом, а реальные исходящие действия не расширяются.

## Цель и инварианты

Аня — один программный персонаж с единым состоянием жизни и настроения. Контакты
имеют отдельные отношения, память и границы; чаты являются лишь контекстами
общения. Миграция обязана сохранять прежние SQLite-данные, быть идемпотентной и
допускать rollback выключением нового флага без удаления новых записей.

Ключевые инварианты:

- Глобальное состояние не принадлежит `chat_id`; отношения и приватные данные —
  принадлежат контакту.
- Каждое существенное событие имеет `actor`, источник, время и причинную связь.
- Пользовательское утверждение о себе не меняет жизнь Ани.
- Решение, запланированное действие и результат исполнения — разные сущности.
- Молчание является допустимым решением; фоновые задачи не блокируют диалог и не
  вызывают LLM без обоснованного повода.
- Новые реальные контакты, блокировки и MTProto не включаются автоматически.

## Аудит существующей архитектуры (этап 1)

### Runtime-путь входящего сообщения

`aiogram` handler → `telegram_updates` deduplication → `messages` →
`IncomingBuffer` → `ReadScheduler`/`ResponseScheduler` → `ConversationManager` →
`ContextBuilder` → OpenAI/OpenRouter provider → appraisal/profile/memory/lifecycle
→ in-memory `ActionQueue` → `TelegramActionExecutor` → Bot API → `messages`.

`ConversationManager` уже передает `chat_id`, turn и generation ID в telemetry;
`ObservabilityWriter` сохраняет закончившиеся генерации и before/after-снимки.
Очередь упорядочена только в пределах одного чата и находится в памяти процесса.

### Текущие источники истины

| Область | Текущий источник | Владелец/проблема миграции |
| --- | --- | --- |
| Сообщения и turns | `messages`, `conversation_turns` | привязаны к chat; `user_id` уже сохраняется для входящих сообщений |
| Легаси-настроение | `emotional_state` singleton | почти не участвует в runtime; не расширять |
| Легаси-эмоции | `emotional_states` | на `chat_id`, используется в context/initiatives |
| Аффективная химия | `affective_states` | на `chat_id`, сейчас главный регуляторный слой |
| Производные эмоции | `affective_profiles` | на `chat_id`, вычисляются из аффективной химии |
| Отношения | `relationship_bonds` | на `chat_id`, стартовые bond/love (.45/.43) слишком высоки для нового контакта |
| Эпизодическая память | `episodic_memories` | на `chat_id`; хорошая relevance/affect фильтрация, но нет contact scope |
| Факты пользователя | `memories` | `(user_id, chat_id)`, безопасно для текущих private chats, но не связывает одного человека в нескольких чатах |
| Биография Ани | `anya_life_events` | уже глобальная; содержит `source_chat_id`, но не actor/causation/scope schema |
| Присутствие/расписание | `daily_presence`, `daily_events` | на `chat_id`, ежедневный шаблон ошибочно создает разные жизни |
| Инициативы | `conversation_lifecycle`, `initiative_history` | на `chat_id`; отдельный scheduler и LLM decision |
| Выполнение действий | `ActionQueue` | in-memory, нет durable outbox или подтвержденного статуса |
| Наблюдаемость | `llm_generations`, `turn_state_observations` | локальная, read-only monitor; не является памятью |

### Компоненты и точки записи

- `emotions/engine.py` — детерминированные legacy-emotions с разными half-life;
  пишет `emotional_states`.
- `emotions/affective.py` — chemistry/appraisal; пишет `affective_states` и
  `affective_profiles`, затем `RelationshipBondManager` пишет `relationship_bonds`.
- `emotions/profile.py` и `expression.py` — вычисляемые эмоции/поведение и
  same-turn expression; не должны становиться вторым primary state.
- `presence.py` и `daily_life.py` — per-chat сон/колледж и LLM-сгенерированные
  дневные события. Это основной источник противоречий между чатами.
- `self_life.py` — глобальные автобиографические события, но не планировщик.
- `initiative/scheduler.py` — самостоятельное первое сообщение, завязанное на
  lifecycle/presence и per-chat limits.
- `conversation/manager.py` — главный оркестратор диалога и переходов;
  `ContextBuilder` уже собирает личность, life, память, эмоции и историю.
- `actions/queue.py` + `telegram/executor.py` — единственный реальный исполнитель,
  который следует расширять вместо создания параллельной очереди.
- `llm/` — один основной вызов для reply, дополнительные bounded вызовы есть для
  timing, initiative, daily life, медиа. OpenRouter session ID и telemetry есть.
- `telegram/` — Bot API allowlist только owner; update idempotency есть через
  `telegram_updates`. MTProto отсутствует.
- `monitor/` — отдельный SQLite read-only процесс; UI не дублирует runtime.

### Найденные риски и порядок устранения

1. Три эмоциональных слоя и relationship state раздельно хранятся per-chat;
   миграция должна вводить новый authoritative слой, а не «усреднять» чаты.
2. `DailyPresenceManager` и `DailyLifeScheduler` делают один и тот же распорядок
   независимым для каждого чата. Их заменит общий world-state с compatibility
   adapter до полного переноса.
3. В очереди действий нет durable status/outbox; сначала добавить совместимый
   persistent journal, затем включать executor на нем.
4. Синхронизация сейчас обеспечивается одной SQLite connection lock в процессе,
   но нет coordinator для конфликтующих global updates. Новый event processor
   станет единственной точкой state-transition.
5. Существующий `relationship_bonds` seeded с высокой близостью. New contacts
   должны получать консервативные значения; исторический основной контакт
   мигрируется без обнуления.
6. `user_id` и `chat_id` в старых private/group событиях недостаточны для полной
   identity map; новый contact record сохраняет stable Telegram user ID и
   связывает известные private chats только при однозначном actor provenance.

## Последовательность работ

| Этап | Зависимости | Результат и критерий |
| --- | --- | --- |
| 1. Audit | — | Эта карта, list of truth/writes/LLM/schedulers, риски миграции |
| 2. Shared state | 1 | `AnyaWorldState`, contacts, feature flags, idempotent migration и runtime adapter |
| 3. Emotion coupling | 2 | global chemistry + contact affect, stable dt decay, seeded long-run simulations |
| 4. Event engine | 2 | append-only event journal, envelopes, idempotency, serialized processor |
| 5. Decision engine | 3,4 | decisions/intentions, explicit no-op/deferral, policy gates |
| 6. World/life | 4,5 | shared location/activity/schedule/commitments and compatibility presence adapter |
| 7. Autonomous thought | 5,6 | adaptive scheduler, budgets and structured concise results |
| 8. Multi-contact memory | 2,4 | scopes/provenance and no private-memory leakage |
| 9. Transport abstraction | 4,10 | Bot adapter retained; MTProto dry-run/auth only after user supplies credentials |
| 10. Durable actions | 4,5 | extend existing queue with persistent lifecycle/outbox and result records |
| 11. Expression | 3,5 | behavior drives actions/voice naturally without emotional exposition |
| 12. Monitor 2.0 | 2–10 | read-only global/contact/event/decision/cost pages |
| 13. Simulator | 2–10 | seeded virtual clock/scenarios/restart/cost checks |
| 14. Production rollout | 2–13 | flags, shadow comparison, allowlisted gradual activation |

## Feature flags and rollout

Flags default to safe compatibility mode. The sequence is: schema and shadow
records → shared state read context → shared affect/contact relationships → event
processor + decision shadow → shared life for one allowed test chat → autonomous
thought with budget → MTProto test account. Real sending to third parties and
real blocking remain disabled unless explicitly enabled.

## Verification protocol

For each completed implementation stage: targeted tests, relevant integration
tests, full `pytest -q`, `python -m compileall -q app`, `git diff --check`, and a
new dated entry in `docs/ANYA_2_PROGRESS.md`. Database migrations are tested on an
empty DB and an old-schema fixture. No commit or push is made without explicit
request and without isolating unrelated working-tree changes.

## Remaining work

- [x] Stage 1 audit and plan
- [x] Stage 2 shared global state, contacts, migration and adapters
- [x] Stage 3 emotional coupling and simulations
- [x] Stage 4 journal/event processor
- [ ] Stage 5 decisions and intentions
- [ ] Stage 6 shared world and commitments
- [ ] Stage 7 background cognition
- [ ] Stage 8 memory scopes
- [ ] Stage 9 transport interface / MTProto dry-run
- [ ] Stage 10 durable action lifecycle
- [ ] Stage 11 expression integration
- [ ] Stage 12 monitor extension
- [ ] Stage 13 simulator
- [ ] Stage 14 shadow-to-production rollout
