# Changelog

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
