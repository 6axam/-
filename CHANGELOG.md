# Changelog

## v1.20.8 — Open loops in initiative
`unreleased` · 2026-10-03

Active unresolved episodes can now appear as compact optional callbacks in initiative context. Their quality adds at most +0.08 to the existing spontaneous probability; no loop bypasses safety gates or resolves itself on send.

## v1.20.7 — Episodic memory relevance and guidance
`unreleased` · 2026-10-03

Episodic retrieval now requires topical relevance, while confidence and open-loop state only refine ranking. Luna receives explicit episode guidance, compact reflections, and can resolve an exposed episode without inventing a replacement.

## v1.20.6 — Emotional sleep recovery
`unreleased` · 2026-10-03

Sleep now deterministically restores fatigue toward a low recovery target, while late-night wakefulness remains a separate source of fatigue.

## v1.20.5 — Flexible canonical image reference
`unreleased` · 2026-10-03

The canonical reference now guides identity while allowing the generation prompt to vary expression, pose, clothing, camera and scene. Appearance guidance uses a subtle neutral smile so a distinctive reference expression is not copied mechanically.

## v1.20.4 — Expanded stable conversation context
`unreleased` · 2026-10-03

Raised the soft input-context target to 14k tokens, enlarged recent history, and added bounded episodic-context settings. Prompt telemetry now includes emotional and voice policy blocks so the component estimate covers the assembled request more faithfully.

## v1.20.3 — Episodic continuity memory
`unreleased` · 2026-10-03

Added chat-scoped episodic memories with immutable post-interaction emotional snapshots, compact open-loop retrieval, and ID-gated resolution/supersession. Episodes reuse the normal Luna response and never make another model request.

## v1.20.2 — Bounded emotional behaviour modifiers
`unreleased` · 2026-10-03

Emotional continuity now makes a small, capped difference to response pacing and spontaneous-initiative probability. Sleep, unread messages, pending replies, cooldowns and daily limits remain hard backend gates.

## v1.20.1 — Luna emotional deltas and context
`unreleased` · 2026-10-03

Luna can now return a sparse bounded emotional delta in her normal structured reply. It is applied only after a successful current generation, while compact per-chat continuity enters conversation and initiative context without an extra model request.

## v1.20.0 — Persistent emotional continuity foundation
`unreleased` · 2026-10-03

Added a deterministic, bounded, per-chat emotional-state engine and SQLite migration. It starts from one canonical baseline, safely applies sparse backend-capped deltas, and evolves through elapsed time without model calls. Application behaviour is intentionally unchanged until the next integration phase.

## v1.19 — One-time later bedtime
`unreleased` · 2026-10-03

Anya can now postpone one upcoming sleep episode after Maxim explicitly asks her to stay awake later. The delay is persisted per chat/night, survives restarts, affects bedtime behavior, and automatically returns to the normal schedule after that night.

## v1.18 — OpenRouter Seed Audio delivery
`unreleased` · 2026-10-01

Outgoing voice messages now use OpenRouter’s confirmed Seed Audio contract with a cached local reference clip and raw MP3 delivery to Telegram. The obsolete direct BytePlus transport and its OGG-only settings were removed; an OpenRouter `LLM_API_KEY` can serve as the voice-key fallback.

## v1.17.2 — Direct voice capability exposure
`531d116` · 2026-10-01

Когда voice runtime доступен, system prompt теперь явно подтверждает реальную Telegram voice-возможность и при прямой просьбе ставит `voice_message` выше tendency; startup логирует состояние без секретов.

## v1.17.1 — Voice speech and render tuning
`a712108` · 2026-10-01

Расширены отдельные voice prompts: Luna получила правила естественной устной русской речи, а Seed Audio — более точный baseline живой Telegram-подачи.

## v1.17 — Seed Audio voice generation
`2ed4934` · 2026-10-01

Подключён configurable BytePlus Seed Audio 1.0 provider: cached local reference, OGG/Opus output, fail-fast config validation, recording-voice presence и отдельные prompt-файлы для spoken style и acoustic delivery.

## v1.16 — Outgoing voice message scaffold
`296f221` · 2026-10-01

Добавлен provider-neutral `voice_message` action: structured voice intent, tendency в prompt, disabled provider, Telegram `send_voice`, persistence spoken text и offline tests. Реальный TTS/API намеренно не подключён.

## v1.15.1 — Bedtime delivery semantics
`b27215d` · 2026-10-01

Bedtime context больше не обходит обычный response timing, а запись о nightly farewell создаётся только после успешной постановки действий в ActionQueue.

## v1.15 — Bedtime ritual
`ff93532` · 2026-10-01

Перед сном Аня получает compact bedtime context в обычном ответе, а idle-chat может один раз за local day пройти через Initiative v2 с отдельным gate и persistent защитой от повторов.

## v1.14.2 — Natural image posing
`3630923` · 2026-10-01

Image prompts теперь отдельно выбирают тип снимка, естественную позу, микродействие и framing; добавлены анти-паттерны против выкрученного тела, неестественных рук и staged influencer поз.

## v1.14.1 — Action tendencies and test record
`a369df1` · 2026-10-01

Стикеры и реакции получили реальные конфигурируемые tendency-настройки, выставленные высокими для текущего runtime; добавлен постоянный журнал карты и времени тестов.

## v1.14 — Human-like spontaneous initiative
`43f3120` · 2026-10-01

Initiative v2 добавляет spontaneous и semantic кандидаты, persistent safety fence для unread/read jobs, privacy-safe daily state, memory/self-life retrieval, variety history и типы инициатив без дополнительных LLM-вызовов.

## v1.13.1 — Version changelog
`b1541cb` · 2026-10-01

Добавлен постоянный журнал версий для всей существующей истории и обязательное правило обновлять его перед каждым следующим commit.

## v1.13 — Self-life autobiography
`9938f31` · 2026-10-01

Аня получила global autobiographical memory и backend-gated короткие продолжения о своей жизни: события сохраняются, извлекаются в контекст и не смешиваются с памятью Максима.

## v1.12 — Active conversation follow-ups
`1c974a9` · 2026-10-01

Недавний ответ Ани теперь ускоряет чтение и ответ в том же чате; college не мешает живому диалогу, а реальные busy/away ограничения сохраняются.

## v1.11.2 — Read boundary race fix
`cbc42d6` · 2026-10-01

После claim read-job перечитывает актуальную границу из SQLite, поэтому сообщения, coalesced во время race, не остаются unread.

## v1.11.1 — Russian memory matching
`655ee89` · 2026-10-01

Память о пользователе стала находиться по частым русским словоформам без fuzzy-поиска; обновления в одном ответе больше не создают duplicate.

## v1.11 — Human-like read delays
`793156b` · 2026-10-01

Добавлена сохраняемая стадия unread → internal read → response. Чтение учитывает сон, колледж, busy/away, объединяет сообщения и переживает restart.

## v1.10.1 — Sleep/wake state fix
`a0cbe5d` · 2026-10-01

Сон и пробуждение теперь вычисляются по текущему времени на каждом вызове, с устойчивым jitter и правильным приоритетом над daily event.

## v1.10 — Canonical life background
`9a94fd6` · 2026-10-01

В основной контекст добавлен компактный постоянный фон жизни Ани: Житомир, колледж, дизайн и домашняя жизнь без заранее придуманной биографии.

## v1.9.2 — College presence rhythm
`4cb73ce` · 2026-10-01

Daily-life события больше не создаются поверх колледжного окна, а wake jitter стал детерминированным между перезапусками.

## v1.9.1 — Daily timing signals
`6d160a6` · 2026-10-01

Timing получил компактные сигналы college/free и privacy-safe daily event; после колледжа лишний timing LLM call больше не выполняется.

## v1.9 — Daily availability timing
`f1628f0` · 2026-10-01

Ответы стали учитывать дневной ритм: утром колледж замедляет обычные ответы, а реальные busy/away события могут объяснять задержку без выдумок.

## v1.8 — Long-term memory
`b35e510` · 2026-10-01

Добавлена chat-scoped долговременная память о пользователе: structured candidates, persistence, retrieval в контекст и защита от небезопасных обновлений.

## v1.7 — Emotional conflict behavior
`c141667` · 2026-09-30

Эмоции Ани получили более реалистичную динамику: ревность и обида могут сохраняться несколько turns без токсичности и шантажа.

## v1.6 — Early typing presence
`fde4bff` · 2026-09-30

Typing начинает показываться во время LLM-запросов и безопасно останавливается при отмене, делая ожидание ответа естественнее.

## v1.5 — Character-aware initiative
`db580d5` · 2026-09-30

Самостоятельные сообщения теперь принимаются с компактным характером, эмоциями и недавним контекстом Ани, а не без personality context.

## v1.4.4 — Compact character prompt
`36cad1e` · 2026-09-30

Характер Ани сжат без потери ключевых черт, заметно уменьшая постоянный размер conversation context.

## v1.4.3 — Compact user profile
`dd34e2a` · 2026-09-30

Профиль Максима сокращён до устойчивых и полезных фактов, чтобы конкретные старые детали не занимали prompt на каждом turn.

## v1.4.2 — Compact response rules
`a9f7119` · 2026-09-30

Инструкции по ответам сокращены до поведенческого контракта; schema и deterministic backend больше не дублируются длинным текстом.

## v1.4.1 — Native structured output
`296372f` · 2026-09-30

Основной LLM request использует native JSON schema вместо постоянного текстового описания, сохраняя repair path для редких ошибок.

## v1.4 — Context budgets and telemetry
`a0de43c` · 2026-09-30

Добавлены token budget для свежей истории и структурированная telemetry состава conversation request без логирования личного текста.

## v1.3.2 — Delayed response cancellation
`6d5d5af` · 2026-09-30

Новое сообщение теперь persistently отменяет старый delayed response, включая claim/cancel race и восстановление после restart.

## v1.3.1 — Stable visual state
`6377847` · 2026-09-30

Image visual state больше не меняет одежду или место внутри одного периода: persisted state определяется стабильными признаками, а не случайным outfit.

## v1.3 — Configuration and image contract
`f05018a` · 2026-09-30

Настройки стали строго валидироваться, temperature реально передаётся provider, а schema, prompt и backend согласованы по поддерживаемым image actions.

## v1.2 — Companion baseline
`9d12b9a` · 2026-09-30

Первый рабочий baseline Telegram AI Companion: conversation flow, SQLite, providers, actions, stickers, media, images, initiative и offline tests.
