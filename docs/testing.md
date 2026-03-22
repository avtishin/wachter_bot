# Testing

## Запуск

```bash
pipenv install --dev
pytest -v                        # все тесты
pytest tests/test_smoke.py       # smoke — быстрая проверка
pytest tests/test_unit.py        # юнит-тесты функций
pytest tests/test_handlers.py    # тесты хендлеров
pytest tests/test_models.py      # интеграция с БД
pytest -k "test_approve"         # один тест по имени
```

92 теста, ~0.9 сек. Реальный Telegram и PostgreSQL не нужны.

## Структура

```
tests/
    conftest.py       # фикстуры, setup БД, sys.path
    helpers.py        # фабрики make_update(), make_message(), make_kick_job()
    test_smoke.py     # импорты, async-корутины, константы, отсутствие хардкодов
    test_unit.py      # чистые функции: authorize_user, filter_message, cancel_kick_jobs и др.
    test_handlers.py  # хендлеры с мок-объектами Update/Context
    test_models.py    # CRUD и session_scope с SQLite
```

## Как устроены тесты

**База данных** — `conftest.py` устанавливает `DATABASE_URL=sqlite:///tests/test.db` до первого импорта `model.py`. Таблицы создаются один раз за сессию, очищаются после каждого теста (autouse-фикстура `clean_db`).

**Telegram API** — заменяется `AsyncMock`:
```python
@pytest.fixture
def mock_bot():
    bot = AsyncMock()
    bot.get_chat_member.return_value.status = "member"
    return bot
```

**БД в хендлерах** — `session_scope` патчится через `patch("actions.session_scope")`:
```python
with patch("actions.session_scope") as mock_scope:
    mock_sess = MagicMock()
    mock_sess.query.return_value.filter.return_value.first.return_value = mock_chat
    mock_scope.return_value.__enter__.return_value = mock_sess
    await on_new_chat_member(update, mock_context)
```

**Job queue** — `mock_job_queue.get_jobs_by_name` настраивается под конкретный тест:
```python
job = make_kick_job(chat_id=-100, user_id=42)
mock_context.job_queue.get_jobs_by_name.side_effect = (
    lambda name: [job] if "kick" in name else []
)
```

## Добавление нового теста

1. Новый хендлер → добавить в `test_handlers.py`
2. Новая вспомогательная функция → добавить в `test_unit.py`
3. Изменение модели → добавить в `test_models.py`
4. Новая константа или проверка структуры → добавить в `test_smoke.py`

Для создания моков Update/Context использовать фабрики из `helpers.py`:
```python
from helpers import make_update, make_kick_job

update = make_update(chat_id=-100, user_id=42, text="привет")
```

## Известные ограничения

- E2E-тесты против реального Telegram API отсутствуют — требуют тестового бота и отдельного чата
- Job-таймеры (`on_kick_timeout`, `on_notify_timeout`) не тестируются напрямую — только факт вызова `job_queue.run_once` с правильными параметрами
