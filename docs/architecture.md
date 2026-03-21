# Architecture

## Overview

Wachter — асинхронный Telegram-бот на `python-telegram-bot v20+` (asyncio). Все хендлеры — `async def`. Бизнес-логика сосредоточена в одном файле `actions.py`.

```
Telegram API
    │  polling (long-polling)
    ▼
Application (bot.py)
    │  маршрутизация по фильтрам
    ▼
Handler (actions.py)          PicklePersistence
    │  await                      │  context.user_data
    ├─ context.bot.*          ◄───┘  (переживает рестарт)
    ├─ context.job_queue.*
    └─ session_scope() ──► PostgreSQL (SQLAlchemy sync)
```

## Модули

| Файл | Ответственность |
|---|---|
| `bot.py` | Инициализация `Application`, регистрация хендлеров, запуск polling |
| `actions.py` | Вся бизнес-логика: хендлеры событий, планировщик таймеров, ответы |
| `model.py` | SQLAlchemy ORM (`Chat`, `User`), `session_scope()` |
| `constants.py` | Шаблоны сообщений, числовые константы, `Actions` enum |
| `custom_filters.py` | Кастомный фильтр: детектирует что добавляют живого пользователя, а не бота |

## Job Queue и именование джобов

Таймеры кика хранятся в APScheduler (in-memory). При рестарте бота все незавершённые таймеры **теряются** — пользователи, которые не успели представиться до рестарта, не будут кикнуты автоматически.

Джобы именуются по схеме, чтобы обеспечить O(1) поиск:

```
kick_{chat_id}_{user_id}    — финальный кик
notify_{chat_id}_{user_id}  — напоминание (за notify_delta минут до кика)
```

Поиск через `context.job_queue.get_jobs_by_name(name)` вместо перебора всей очереди.

Отмена таймеров — через `cancel_kick_jobs(bot, job_queue, chat_id, user_id)`.

## Персистентность состояния

Используется `PicklePersistence` — состояние admin-диалогов (`context.user_data`) сохраняется на диск после каждого апдейта.

```python
context.user_data["action"]   # какую настройку сейчас меняет этот пользователь
context.user_data["chat_id"]  # для какого чата
```

Путь к файлу: `data/bot_persistence` (переопределяется через `PERSISTENCE_PATH`).

На Railway необходим **Volume**, смонтированный в `/app/data`, иначе файл теряется при редеплое.

## Известное ограничение: блокирующий DB I/O

Функции `filter_message`, `is_new_user`, `is_chat_filters_new_users` — синхронные и вызывают `session_scope()` (блокирующий SQLAlchemy) прямо из async-хендлеров. Это блокирует event loop на время каждого запроса к БД.

Для текущего масштаба (один чат или несколько) это приемлемо. При высокой нагрузке — обернуть в `asyncio.get_event_loop().run_in_executor()` или мигрировать на `sqlalchemy.ext.asyncio`.

## Авторизация

Проверка прав: `await authorize_user(bot, chat_id, user_id)` — запрашивает статус пользователя через Telegram API (`creator` или `administrator`). Вызывается:
- В хендлерах команд (`/skip`, `/approve`) — проверяет автора команды
- В `on_button_click` — проверяет нажавшего кнопку перед изменением настроек
- В `on_message` (ветка настроек DM) — проверяет перед применением изменений

## Переменные окружения

| Переменная | Обязательна | Описание |
|---|---|---|
| `TELEGRAM_TOKEN` | да | Токен бота от @BotFather |
| `DATABASE_URL` | нет | Строка подключения к PostgreSQL (по умолчанию `postgresql://localhost:5432/wachter`) |
| `PERSISTENCE_PATH` | нет | Путь к файлу PicklePersistence (по умолчанию `data/bot_persistence`) |
