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

## Реализовано в core MVP

- debounce входящих сообщений и сохранение отдельных Telegram-сообщений;
- типизированный JSON-контракт LLM, валидация и безопасный fallback;
- последовательная очередь действий с typing, задержками и отменой предыдущей генерации;
- SQLite-хранилище входящих и исходящих сообщений, turns и idempotency по `chat_id + message_id`;
- whitelist одного владельца по `OWNER_TELEGRAM_ID`;
- OpenAI/OpenRouter-compatible provider с timeout, retry временных ошибок и одной попыткой repair сломанного JSON;
- pytest unit и offline integration tests без реальных Telegram/LLM credentials.

Память, summaries, stickers и инициативность намеренно не входят в этот этап.
