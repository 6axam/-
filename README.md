# Telegram AI Companion

Рабочий core MVP Telegram-собеседника: LLM возвращает JSON-план действий, а backend последовательно выполняет сообщения с typing/паузами и отменяет неотправленный хвост ответа при новом сообщении пользователя.

## Запуск

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
# заполните обязательные переменные ниже
.venv/bin/python -m app.main
```

Заполни `TELEGRAM_BOT_TOKEN`, `OWNER_TELEGRAM_ID`, `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL`. `LLM_BASE_URL` оставь пустым для стандартного URL OpenAI/OpenRouter или укажи OpenAI-compatible endpoint вручную.

## Отдельный Telegram-аккаунт (MTProto, offline foundation)

Bot API остаётся рабочим режимом по умолчанию. Для отдельного аккаунта Ани
создай API credentials в [my.telegram.org](https://my.telegram.org), добавь
`TELEGRAM_API_ID` и `TELEGRAM_API_HASH` только в локальный `.env`, затем установи
обновлённые зависимости и выполни локальный вход:

```bash
./.venv/bin/pip install -r requirements.txt
./.venv/bin/python -m app.telegram.mtproto login
./.venv/bin/python -m app.telegram.mtproto status
```

Команда запросит номер, код и при необходимости 2FA **в локальном терминале**.
Не присылай их в чат и не коммить `.env` или `data/mtproto/*.session`. Login не
запускает replies и не пишет сторонним людям. Live smoke test доступен только
для Saved Messages и требует `TELEGRAM_MTPROTO_DRY_RUN=false` и явного флага:

```bash
./.venv/bin/python -m app.telegram.mtproto smoke-test --confirm-saved-messages
```

Если код не приходит, а аккаунт Ани уже открыт в официальном Telegram на
телефоне, можно войти QR-сканированием — без номера, SMS и передачи кода:

```bash
./.venv/bin/python -m app.telegram.mtproto login-qr
```

На телефоне открой Telegram → **Настройки → Устройства → Подключить устройство**
и отсканируй QR из терминала. QR быстро обновляется; его URL в терминал не
печатается.

### Тест реального аккаунта: allowlisted private text

После успешного smoke test можно временно переключить именно основной runtime
на отдельный аккаунт. В локальном `.env` укажи:

```env
TELEGRAM_TRANSPORT=mtproto
TELEGRAM_MTPROTO_ENABLED=true
TELEGRAM_MTPROTO_DRY_RUN=false
TELEGRAM_ALLOWLIST=<твой числовой Telegram user ID>
TELEGRAM_AUTO_REPLY_UNKNOWN=false
TELEGRAM_GROUPS_ENABLED=false
TELEGRAM_AUTONOMOUS_OUTBOUND=false
```

Затем обычный запуск:

```bash
./.venv/bin/python -m app.main
```

В этом первом live-срезе Аня принимает **только текстовые личные сообщения**
только от ID в allowlist, сохраняет и отвечает через уже существующие
память/эмоции/LLM/очередь действий. Группы, неизвестные контакты, инициативы и
автономные исходящие отключены. MTProto история хранится в отдельном внутреннем
пространстве чатов, поэтому не смешивается с историей Bot API. Для возврата к
старому боту поставь `TELEGRAM_TRANSPORT=bot_api`.

## Реализовано в core MVP

- debounce входящих сообщений и сохранение отдельных Telegram-сообщений;
- типизированный JSON-контракт LLM, валидация и безопасный fallback;
- последовательная очередь действий с typing, задержками и отменой предыдущей генерации;
- SQLite-хранилище входящих и исходящих сообщений, turns и idempotency по `chat_id + message_id`;
- whitelist одного владельца по `OWNER_TELEGRAM_ID`;
- OpenAI/OpenRouter-compatible provider с timeout, retry временных ошибок и одной попыткой repair сломанного JSON;
- pytest unit и offline integration tests без реальных Telegram/LLM credentials.

Проект включает компактную память о пользователе, эпизодическую continuity-память с незакрытыми темами, стикеры и безопасную инициативность. Эмоциональное состояние сохраняется отдельно для каждого чата и влияет на тон только в ограниченных backend-рамках.

## Терминальный тамагочи

Отдельный Textual-интерфейс показывает текущее сохранённое состояние Ани, не запуская второй бот:

```bash
cd "/home/maks/Documents/ChatGPT/собеседник"
./.venv/bin/python -m app.tui
```

Он открывает настроенную SQLite-базу только для чтения (`mode=ro` и `query_only`), примерно раз в секунду перечитывает расписание, активное событие, conversation lifecycle, affective state, relationship bond и эпизодическую память. Анимация сцены идёт независимо с частотой 6 FPS. Интерфейс не вызывает LLM, не отправляет сообщения в Telegram, не создаёт воспоминания и не меняет runtime state.

Визуальный слой рисует цветные мини-диорамы размером 42×14: домашнюю комнату с кроватью, рабочее место, аудиторию, ночной сон, сцену с телефоном и улицу. У каждой локации своя палитра и набор предметов, а Аня показана небольшим чиби-спрайтом в подходящей позе. Доминирующая эмоция добавляет цветные частицы прямо в сцену; их количество и яркость зависят от интенсивности сохранённого состояния.

Общая уютная semantic palette объединяет Textual chrome и Rich-графику. Truecolor используется при поддержке терминалом, 256-color получает индексированную палитру, а ограниченный или `NO_COLOR`-терминал — монохромный режим с bold/dim/reverse. Для не-UTF-8 окружений renderer заменяет block/box glyphs читаемыми ASCII-символами.

Сцена выбирается в порядке: сон, колледж, известное событие с телефоном, известная домашняя активность за столом, свободное время дома, занятость/отсутствие. Вариации idle-позы — только визуальные и явно отмечены как такие; они не становятся фактом об активности Ани. Состояние непосредственной генерации обычного ответа не доступно другому процессу, поэтому индикатор ответа включается только для реально сохранённого `scheduled_responses.status='processing'` (а чтения — для `scheduled_reads`).

Клавиши: `q` — выход, `r` — обновление, `e` — эмоциональные каналы, `m` — активные эпизоды отношений, `l` — лента событий текущего запуска, `?` — помощь, `Esc` — закрыть панель. На узком терминале включается компактная сцена с краткими energy/trust/security; подробные эмоции и эпизоды остаются доступны через `e` и `m`.
