# Testing

Два независимых набора: бот (SQLite, без Telegram) и скрейпер/дашборд
(эфемерный Postgres через testcontainers).

## Запуск

```bash
# Бот — 168 тестов, ~1.3 сек, без реального Telegram/PostgreSQL (SQLite)
pipenv install --dev
pytest -v
pytest tests/test_whois.py            # кнопочная анкета
pytest -k "test_approve"              # один тест

# Скрейпер/дашборд — 33 теста, эфемерный Postgres (нужен Docker)
cd nes_directory && pip install -r requirements.txt
pytest -q
```

## Тесты бота (`tests/`)

| Файл | Покрывает |
|---|---|
| `test_smoke.py` | импорты, async-корутины, константы, структура схемы |
| `test_unit.py` | чистые функции: `authorize_user`, `mention_markdown`, `cancel_kick_jobs` и др. |
| `test_handlers.py` | хендлеры с мок Update/Context (вход, кик, regex, настройки) |
| `test_models.py` | ORM/`session_scope` (SQLite); дефолты и покрытие плейсхолдеров Markdown |
| `test_whois.py` | кнопочная анкета: категории, годы, «Назад», валидация, завершение |
| `test_alumni_bot.py` | узнавание выпускников, `classify`, `identity_greeting`, recap |
| `test_start.py` | `/start` из таблицы chats, bootstrap chats-строки, детект выхода, покрытие меню |
| `test_security.py` | `escape_md_v1`/`safe_mention` (markdown-инъекция), `split_message` |
| `test_logging.py` | httpx/httpcore на WARNING (токен не течёт в логи) |

## Как устроены (бот)

- **БД** — `conftest.py` ставит `DATABASE_URL=sqlite:///tests/test.db` до импорта
  `model.py`; таблицы создаются раз за сессию, чистятся autouse-фикстурой
  `clean_db`. Read-models общих таблиц заданы как generic JSON, поэтому работают
  на SQLite.
- **Telegram** — `AsyncMock`. Фикстуры `mock_bot`/`admin_bot` задают статус и
  `user.full_name/id` (для `safe_mention`). `_bot_has_rights` (autouse) патчит
  `ensure_rights → True`.
- **Фабрики** — `helpers.py`: `make_update`, `make_message`, `make_kick_job`;
  `telegram_markdown_ok` валидирует итоговый Markdown (ловит инъекции/битые теги).

## Тесты скрейпера/дашборда (`nes_directory/tests/`)

`conftest.py` поднимает эфемерный Postgres (testcontainers), прогоняет `init_db`.
Покрывают: `alumni_derive`/`alumni_link` (парсинг, привязка, classify, reconcile),
`nes_db.ingest` (diff/история), `seed_members`, `translit`, и Flask-роуты
дашборда (`test_dashboard`, `test_identities`: resolve/category/edit).

## Добавление теста

- Новый хендлер бота → `test_handlers.py`; шаг анкеты → `test_whois.py`;
  узнавание/приветствия → `test_alumni_bot.py`; модель → `test_models.py`.
- Меняешь тексты/поведение приветствий — обнови ассерты в `test_whois.py`
  (`_finish_declared`) и `test_alumni_bot.py` (`identity_greeting`).
- Любой пользовательский текст в MARKDOWN-сообщении — прогони через
  `telegram_markdown_ok` (иначе инъекция проскочит мимо).

## Ограничения

- E2E против реального Telegram нет (нужен тестовый бот и чат).
- Job-таймеры проверяются по факту вызова `job_queue.run_once`, не по срабатыванию.
- Таймеры теряются при рестарте бота (PicklePersistence не хранит job_queue).
