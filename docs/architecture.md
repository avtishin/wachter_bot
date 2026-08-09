# Architecture

## Overview

Экосистема из двух сервисов на **одном PostgreSQL**:

- **Бот** (`wachter/`) — асинхронный `python-telegram-bot v22` (asyncio, все
  хендлеры `async def`). Модерация чатов + узнавание выпускников РЭШ.
- **Дашборд + скрейпер** (`nes_directory/`) — Flask-панель и парсер каталога
  my.nes.ru.

```
Telegram API                         my.nes.ru
    │ long-polling                       │ requests + BeautifulSoup
    ▼                                     ▼
Application (wachter/bot.py)         nes_scraper.py → out/alumni.json
    │  фильтры/хендлеры                   │  nes_db.ingest()
    ▼                                     ▼
actions.py/whois.py/alumni.py    ┌──────────────────────────────┐
    └─ session_scope() ─────────►│         PostgreSQL           │◄── Flask (app.py)
                                 │ chats/users (бот, Alembic)   │    дашборд
                                 │ alumni_*/tg_identity (скрейпер)│
                                 └──────────────────────────────┘
```

Бот polling'ит только нужные типы апдейтов
(`message/edited_message/callback_query/my_chat_member/chat_member`) — лёгкий,
без постоянного сканирования чатов.

## Модули бота (`wachter/`)

| Файл | Ответственность |
|---|---|
| `bot.py` | `Application`, регистрация хендлеров, `run_polling(allowed_updates=…)` |
| `actions.py` | Хендлеры событий, планировщик таймеров, админ-меню, `safe_mention`/`escape_md_v1` |
| `whois.py` | Кнопочная анкета (state machine в `chat_data`), `_finish_declared` |
| `alumni.py` | Узнавание выпускников (по нику/почте), классификация, `format_greeting`, recap |
| `model.py` | ORM `Chat`, `User` + read-models общих таблиц (`TgIdentity`, `AlumniPerson`, `AlumniProgram*`); `session_scope()` |
| `constants.py` | Дефолтные шаблоны, `Actions` enum для callback_data |
| `custom_filters.py` | Фильтр: добавлен живой пользователь (не бот) |

Схему `chats`/`users` владеет бот (Alembic, `migrations/`). Схему
`alumni_*`/`tg_identity` владеет скрейпер (`nes_directory/alumni_models.py`,
`init_db`/`bootstrap.py`); бот их только читает/пишет через read-models
(generic JSON, чтобы SQLite-тесты работали).

## Данные

- `tg_identity` — **глобальная** идентичность Telegram-пользователя (PK `user_id`):
  категория (`alumni/student/unresolved_alumni/friend/employee/unknown`),
  `alumni_uid` (привязка), `declared_*`, `intro`, `username`.
- `users(user_id, chat_id)` — **членство по чатам** (кого бот видел). Один
  человек = 1 `tg_identity` + N строк `users`.
- `chats` — конфиг чата (тексты, таймауты, regex, `title`).
- `alumni_person` (+ `_history`/`_change_log`/`_crawl`/`_raw_card`),
  `alumni_program`, `alumni_program_year` — директория с историей версий.

## Job Queue

Таймеры кика в APScheduler (in-memory). При рестарте бота незавершённые таймеры
**теряются** (PicklePersistence не хранит job_queue) — известное ограничение.
Джобы именуются для O(1) поиска:

```
kick_{chat_id}_{user_id}    — финальный кик
notify_{chat_id}_{user_id}  — напоминание закончить анкету (за notify_delta мин.)
```

Отмена — `cancel_kick_jobs(bot, job_queue, chat_id, user_id)`; поиск —
`get_jobs_by_name(name)`.

## Безопасность вывода

Бот шлёт сообщения legacy-Markdown'ом (`ParseMode.MARKDOWN`). v1 не умеет
экранировать `]`, поэтому весь подконтрольный пользователю текст (имя, анкета)
проходит через `escape_md_v1` (удаляет `]`, экранирует `_*[\``), а упоминания
строятся через `safe_mention` — иначе markdown/фишинг-инъекция через имя. «Текущие
настройки» шлются plain-text (устойчиво к любым правкам админов).

## Персистентность и авторизация

- `PicklePersistence` (`context.user_data`: какую настройку и в каком чате правит
  админ). Путь — `PERSISTENCE_PATH` (по умолчанию `data/bot_persistence`). На
  Railway нужен **Volume** в `/app/data`.
- `authorize_user(bot, chat_id, user_id)` → `creator`/`administrator` (запрос к
  Telegram). Проверяется в командах, `on_button_click` и при сохранении настройки.
- `/start` перечисляет чаты из таблицы **`chats`** (где бот присутствует),
  гейтит по `authorize_user` — представляться через #whois не нужно.
- Права бота: `ensure_rights` требует админ + `can_restrict_members` +
  `can_delete_messages`, иначе предупреждает один раз и не обрабатывает чат.

## Переменные окружения

| Переменная | Сервис | Описание |
|---|---|---|
| `TELEGRAM_TOKEN` | бот | Токен @BotFather (один токен = один инстанс!) |
| `DATABASE_URL` | оба | PostgreSQL (общий); `postgres://` → `postgresql://` |
| `PERSISTENCE_PATH` | бот | Путь PicklePersistence (по умолчанию `data/bot_persistence`) |
| `APP_USER`/`APP_PASSWORD` | дашборд | Basic Auth |
| `NES_LOGIN`/`NES_PASSWORD` | дашборд | Доступ к my.nes.ru для скрейпа |

Деплой: [../DEPLOY.md](../DEPLOY.md). Флоу: [flows.md](flows.md).
