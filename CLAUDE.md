# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Wachter — асинхронный Telegram-бот для модерации групповых чатов. Требует от новых участников представиться с тегом `#whois` в течение настраиваемого таймаута, автоматически кикает тех, кто не представился. Администраторы настраивают бота через inline-кнопки в личке.

## Commands

```bash
# Зависимости
pipenv install

# Локальный запуск
DATABASE_URL=postgresql://localhost:5432/wachter TELEGRAM_TOKEN=<token> python wachter/bot.py

# БД миграции
alembic upgrade head
alembic revision --autogenerate -m "description"

# Docker
docker build -t wachter_bot .
docker run -e TELEGRAM_TOKEN=<token> -e DATABASE_URL=<url> wachter_bot

# Тесты (PostgreSQL не нужен)
pipenv install --dev
pytest -v
pytest tests/test_smoke.py     # быстрая проверка
pytest -k "test_approve"       # один тест
```

Dockerfile автоматически запускает `alembic upgrade head` перед стартом бота.

## Architecture

Весь исходный код в `wachter/`. Все хендлеры — `async def` (python-telegram-bot v20+).

- **`bot.py`** — точка входа: `Application` с `PicklePersistence`, регистрация хендлеров, polling
- **`actions.py`** — вся бизнес-логика: async-хендлеры, планировщик таймеров кика, ответы
- **`model.py`** — SQLAlchemy ORM (`Chat`, `User`), `session_scope()` для работы с БД
- **`constants.py`** — шаблоны сообщений, `Actions` enum для callback_data inline-кнопок
- **`custom_filters.py`** — фильтр: пропускает только добавление живых пользователей (не ботов)

Подробнее: [docs/architecture.md](docs/architecture.md)

## Key Flows

**Новый участник:** приветствие → планируется notify + kick джоб → пользователь пишет `#whois` (≥20 символов) → записывается в БД, джобы отменяются → иначе таймер истекает, кик.

**Настройка:** `/start` в личке → выбор чата → inline-меню → `context.user_data` хранит ожидаемое поле → следующее сообщение обновляет настройку в БД.

**Regex-фильтр:** каждое сообщение проверяется против `chat.regex_filter`; совпадение → удаление + кик (с учётом `filter_only_new_users`).

Детальные диаграммы: [docs/flows.md](docs/flows.md)

## Database

PostgreSQL + SQLAlchemy. Две таблицы:
- `chats` — конфиг чата: шаблоны, `kick_timeout`, `regex_filter`, `filter_only_new_users`
- `users` — составной PK `(user_id, chat_id)`, поле `whois` с текстом представления

Всегда использовать `session_scope()` из `model.py` — автоматически commit/rollback/close.

## Job Queue

Джобы именуются `kick_{chat_id}_{user_id}` и `notify_{chat_id}_{user_id}` для O(1) поиска через `get_jobs_by_name()`. Отмена — через `cancel_kick_jobs(bot, job_queue, chat_id, user_id)`. При рестарте бота все pending таймеры теряются.

## Admin Commands

`/skip` — отменяет таймер, не пишет в БД (при следующем входе снова попросят представиться).
`/approve` — пишет в БД + отменяет таймер (при следующем входе будет как знакомый).

Справочник команд и плейсхолдеров: [docs/admin-reference.md](docs/admin-reference.md)

## Testing

76 тестов, ~0.7 сек, без реального Telegram и PostgreSQL. Используется SQLite.
Подробнее о конвенциях и добавлении тестов: [docs/testing.md](docs/testing.md)
