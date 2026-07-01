# Alumni-aware Wachter bot — Design Spec

**Date:** 2026-07-01
**Status:** approved for planning

## Goal

Научить Wachter-бота узнавать выпускников РЭШ по телеграму, сверяясь с базой
директории my.nes.ru. Новый участник чата:

- **найден в базе выпускников** → бот приветствует его данными из базы (имя +
  класс, напр. `MAE'19`) и НЕ требует представляться (кик не ставится);
- **не найден** → кнопочный whois (категория → программа → год → имя), затем
  классификация (выпускник / студент / друг / сотрудник); молчит 30 мин → кик
  (стандартный флоу).

Со временем профили «сходятся»: когда база выпускников обновляется (студент
выпустился или выпускник добавил свой телеграм), ранее неопознанные люди
автоматически привязываются к записи выпускника.

## Ключевые решения (согласовано)

1. **Сопоставление — в момент входа.** Матчим по `username` при входе, сразу
   сохраняем стабильный `user_id`. Никаких внешних API/userbot (Bot API не умеет
   резолвить username→id; это осознанно не используем).
2. **Единый Postgres на Railway.** Скрапер, дашборд и бот ходят в одну БД через
   `DATABASE_URL`. Alumni-хранилище мигрируется c SQLite на SQLAlchemy/Postgres,
   включая сырые карточки (чтобы переживать редеплой на эфемерной ФС).
3. **Кнопочный whois** для ненайденных: категория/программа/год — тапами
   (списки строятся динамически из базы), имя — одно текстовое поле.
4. **Классификация** по динамическим порогам из базы (никакого хардкода годов).
5. **Тесты** — целиком на эфемерном Postgres (весь существующий bot-suite тоже
   переводится с SQLite на Postgres).

---

## 1. Хранилище и модель данных (Postgres)

Единый Postgres. Владение схемой между двумя кодбейзами (`wachter/` — бот,
`nes_directory/` — скрапер+дашборд):

- `alumni_*` (включая `alumni_program_years`) создаёт, мигрирует и наполняет
  **скрапер**; бот их только читает.
- `tg_identity` и колонку `chats.on_alumni_welcome_message` создаёт **бот**
  (Alembic); дашборд пишет `tg_identity` при ресолве.
- Договорённость об именах таблиц/колонок ниже — источник истины.

### Alumni (перенос из SQLite, префикс `alumni_`)

**`alumni_person`** — текущий срез директории:

| колонка | тип | назначение |
|---|---|---|
| `uid` | TEXT PK | id персоны в директории |
| `name` | TEXT | полное имя как в директории |
| `first_name`, `last_name` | TEXT | разбор из `name` (для приветствий/матчинга) |
| `sex`, `birthday`, `residence` | TEXT | как в директории |
| `telegram_username` | TEXT, индекс (lower) | ник из `t.me/…` ссылки (нормализованный, без `@`) |
| `programs` | JSONB | список программ (титулы) |
| `classes` | JSONB | список классов (`MAE'2019`, …) |
| `grad_year_max` | INT | максимальный год выпуска (для порогов) |
| `content_hash` | TEXT | sha256 канонизированной записи |
| `full` | JSONB | полная распарсенная карточка (для дашборда) |
| `first_seen`, `last_seen`, `last_changed`, `removed_at` | TIMESTAMPTZ | жизненный цикл |

**`alumni_person_history`** — append-only версии (uid, content_hash, captured_at, data JSONB).
**`alumni_change_log`** — пофилдовые диффы (uid, captured_at, change_type, field, old_value, new_value).
**`alumni_crawl`** — журнал запусков (started_at, finished_at, kind, n_seen, n_new, n_changed, n_removed).
**`alumni_raw_card`** — сырые карточки (uid PK, html TEXT, fetched_at) — замена файлам `raw_html/cards/`.
**`alumni_program_years`** — справочник для клавиатур (program_code TEXT, year INT), пересобирается на каждом `ingest` из `classes`; PK (program_code, year). Также хранит `alumni_program(code, title)` для подписей кнопок.

`telegram_username` извлекается при ingest из `contact.links` (`t.me/<user>` или
`telegram.me/<user>`), нормализуется: lower-case, без `@`, без query.

### Идентичность / привязка (новое, глобально — не по чатам)

**`tg_identity`** — кто этот телеграм-человек по отношению к РЭШ:

| колонка | тип | назначение |
|---|---|---|
| `user_id` | BIGINT PK | стабильный telegram id |
| `username` | TEXT, nullable | последний известный ник (нормализованный) |
| `category` | TEXT | `alumni` / `student` / `friend` / `employee` / `unresolved_alumni` / `unknown` |
| `alumni_uid` | TEXT FK→alumni_person, nullable | привязка к выпускнику |
| `declared_name` | TEXT | имя, введённое в whois (для матчинга) |
| `declared_program` | TEXT, nullable | код программы из кнопок |
| `declared_year` | INT, nullable | год из кнопок |
| `intro` | TEXT, nullable | полный текст, который ввёл человек |
| `first_seen`, `last_seen`, `verified_at` | TIMESTAMPTZ | |
| `source` | TEXT | `members_csv` / `join` / `buttons` / `manual` |

### Бот (существующее)

- `chats` — добавить `on_alumni_welcome_message TEXT NOT NULL DEFAULT
  '%NAME%, %CLASS% — добро пожаловать! 🎓'`.
- `users` (per-chat whois/кик) — без изменений; используется как и раньше, чтобы
  повторный вход давал «известное» приветствие и не было кика.

---

## 2. Флоу входа и классификация

### Алгоритм `on_new_chat_member` (на каждого нового `user_id`, + `username` если есть)

1. **`tg_identity` по `user_id` существует** и `category` определена (не
   `unknown`) → приветствуем по категории (см. §4), кик не ставим, обновляем
   `username`/`last_seen`.
2. **Иначе, есть `username`** → ищем `alumni_person` по `telegram_username`:
   - **нашли** → пишем `tg_identity(category='alumni', alumni_uid, source='join')`,
     `users`-запись (известен), приветствие выпускника; кик не ставим.
3. **Иначе (не нашли или нет username)** → **кнопочный whois** + таймер кика
   (`kick_timeout`, по умолчанию 30 мин, как сейчас).

Существующий `#whois`-хэштег остаётся запасным путём: снимает кик, текст пишем в
`intro`, `category='unresolved_alumni'` (разбор в дашборде). Основной путь для
новичков — кнопки.

### Кнопочный whois (минимум текста)

Состояние диалога — в `context.user_data` по ключу `(chat_id, user_id)`.
Callback-кнопки проверяют, что нажал именно целевой новичок (иначе игнор).

1. **Категория:** `[🎓 Я выпускник]` · `[📚 Студент РЭШ]` · `[🤝 Друг / сотрудник РЭШ]`
2. **Выпускник / Студент → программа:** кнопки строятся из
   `SELECT DISTINCT program_code FROM alumni_program_years` (по 2 в ряд, подпись
   из `alumni_program.title`).
   **Друг/сотрудник →** `[Друг РЭШ]` · `[Сотрудник РЭШ]` (в `employee`/`friend`).
3. **Год** (для выпускника/студента): десятилетие → год. Список годов —
   `SELECT year FROM alumni_program_years WHERE program_code=?`.
   - **Выпускник:** строго годы из базы.
   - **Студент** («ожидаемый выпуск»): годы из базы **плюс будущие**:
     `BAE → +4 года`, остальные программы `→ +2 года` (от `max(year)` программы),
     плюс кнопка `[пропустить]`.
4. **Имя:** одно текстовое поле «Фамилия Имя (+ по желанию пара слов)». Первая
   строка/значение → `declared_name`; весь текст → `intro`.
5. Отправка имени = завершение: **снимает кик**, пишет `tg_identity`
   (`source='buttons'`), шлёт приветствие (§4).

Итог: 2–4 тапа + одна строка имени. Имя обязательно (телеграм-профиль ненадёжен —
ники мусорные), оно нужно для последующего матчинга.

### Классификация (динамические пороги)

`maxYear = SELECT MAX(grad_year_max) FROM alumni_person` (сейчас 2025, растёт сам).

- Найден по нику в директории → **`alumni`** (verified, есть `alumni_uid`).
- Заявлен выпускник + программа + год:
  - `год > maxYear` → **`student`** (ещё не в базе; сойдётся при обновлении);
  - `год ≤ maxYear`, но по нику не нашли → **`unresolved_alumni`** (должен быть в
    базе — очередь на ресолв в дашборде).
- Кнопка «Студент РЭШ» → **`student`**.
- «Друг РЭШ» → **`friend`**; «Сотрудник РЭШ» → **`employee`**.

---

## 3. Реконсиляция и сид из members.csv

### Реконсиляция (после каждого `ingest`/обновления базы)

Для всех `tg_identity` с `alumni_uid IS NULL` и `category IN
('student','unresolved_alumni','unknown')`:

- **По `username`** (авто, высокая точность): совпал с
  `alumni_person.telegram_username` → `alumni_uid`, `category='alumni'`,
  `verified_at=now`. Закрывает оба кейса: студент выпустился и появился в базе;
  выпускник добавил свой телеграм.
- **По `declared_name` + программа + год** (когда ника нет/не совпал): фаззи-матч
  по имени и классу → НЕ авто, а кандидат на подтверждение в дашборде.

### Сид из members.csv (разовый скрипт `seed_members.py`)

- Все 647 участников → upsert `tg_identity(user_id, username, source='members_csv')`.
- 228 совпавших по нику → `category='alumni'`, `alumni_uid`.
- Остальные (419, из них 38 без ника) → `category='unknown'` (id+username есть для
  будущего; классификация позже вручную/при активности).

---

## 4. Приветствия и дашборд

### Шаблоны

- `chats.on_alumni_welcome_message` (новый) для найденных выпускников.
  Плейсхолдеры: `%NAME%`, `%FIRST_NAME%`, `%LAST_NAME%`, `%CLASS%`, `%PROGRAM%`.
  Дефолт: `%NAME%, %CLASS% — добро пожаловать! 🎓`.
- Завершившие кнопочный флоу (student/friend/employee) → существующий
  `on_introduce_message`.
- Редактирование нового шаблона — в существующем inline-меню настроек бота
  (`on_start_command` → `on_button_click`), рядом с прочими шаблонами.

### Дашборд (nes_directory, теперь Postgres) — вкладка «Идентичности»

- Список `tg_identity` с фильтром по категории; отдельная очередь
  `unresolved_alumni` и кандидатов из фаззи-матча.
- Действие «Привязать»: поиск выпускника → подтверждение → `alumni_uid` +
  `category='alumni'`.
- Ручная смена категории; просмотр `declared_name/program/year/intro`.
- Счётчики сида из members.csv.

---

## 5. Компоненты, фазы, тестирование

### Файлы

**Скрапер (`nes_directory/`):**
- `nes_db.py` → SQLAlchemy/Postgres; таблицы `alumni_*`; derived-поля
  (first/last name, telegram_username, grad_year_max); пересборка
  `alumni_program_years`; сырые карточки в `alumni_raw_card`.
- `nes_scraper.py` → парсинг без изменений; download пишет сырьё в БД.
- `reconcile.py` (новый) → реконсиляция (вызывается из ingest и вручную).
- `seed_members.py` (новый) → разовый импорт members.csv.
- `app.py` → Postgres + вкладка «Идентичности» + ресолв.
- `docker-compose.yml` → добавить Postgres (локалка) и `test`-Postgres.

**Бот (`wachter/`):**
- `model.py` → `TgIdentity`, read-модель alumni, `Chat.on_alumni_welcome_message`,
  справочники для клавиатур.
- `actions.py` → переписать `on_new_chat_member`; кнопочный whois-флоу
  (callback-хендлеры + состояние в `user_data`); приветствия.
- `constants.py` → новые `Actions` для кнопок категории/программы/года.
- `bot.py` → регистрация новых callback-хендлеров.
- Alembic-миграция (`tg_identity`, `alumni_program_years`,
  `chats.on_alumni_welcome_message`).

### Фазы (мёржатся по отдельности)

1. Миграция alumni-хранилища на Postgres; дашборд на Postgres; ре-ингест данных.
2. `tg_identity` + `alumni_program_years` + сид members.csv + `reconcile.py`.
3. Бот: матчинг выпускника на входе + приветствие + пропуск кика.
4. Бот: кнопочный whois для ненайденных + классификация.
5. Дашборд: вкладка «Идентичности» + ресолв.
6. Деплой обоих сервисов на Railway (один Postgres).

### Тестирование

- Весь тест-suite на **эфемерном Postgres** (docker `test`-сервис/`testcontainers`,
  схема через Alembic/`create_all`). SQLite убираем полностью, включая перевод
  существующих ~95 тестов (правка `conftest`).
- Новые тесты: matched→приветствие/без кика; not-found→кнопки→категория/год/имя;
  классификация по динамическому `maxYear`; реконсиляция по нику (авто) и по
  имени (кандидат); сид members.csv; будущие годы BAE +4 / прочие +2.

---

## Data flow

```
[my.nes.ru] --scrape--> nes_scraper.py --parse--> nes_db.ingest
   -> Postgres: alumni_person(+history/changelog), alumni_raw_card, alumni_program_years
   -> reconcile.py: подтягивает tg_identity по username
[Telegram join] -> actions.on_new_chat_member
   -> lookup tg_identity(user_id) | alumni_person(telegram_username)
   -> greet (alumni) | button-whois -> tg_identity(+category)
[Dashboard] -> читает alumni_* / tg_identity, пишет ресолвы в tg_identity
```

## Edge cases / обработка ошибок

- **Нет username при входе** → сразу кнопочный whois (шаг 3).
- **Ник сменился** → ключ `user_id` стабилен; `username` обновляем на входе;
  реконсиляция по новому нику при следующем обновлении базы.
- **Несколько выпускников с одним ником** (редко) → берём первого, помечаем
  конфликт для дашборда.
- **Выпускник в нескольких программах/классах** → `%CLASS%` = соединение классов
  (напр. `BAE'17, MAE'19`).
- **Кнопку нажал не тот пользователь** → сверяем `callback.from_user.id` с целевым
  новичком, чужие нажатия игнорируем.
- **Человек ушёл/молчит в процессе кнопок** → таймер кика срабатывает штатно.
- **Повторный вход** → «известен» через `tg_identity`/`users`.
- **Нет прав у бота** → чат мьютится (существующий `_mute_chat`/`on_error`).
- **Пустой ответ базы / БД недоступна** → фолбэк на стандартный whois-флоу, ошибки
  логируются, вход не роняется.

## Открытые вопросы

Нет — все решения зафиксированы выше.
