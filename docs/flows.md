# Flows

## Новый участник (`on_new_chat_member`)

Права бота проверяются первыми (`ensure_rights`) — без них выходим.
Дальше по порядку:

```
Участник вступает в чат
    │  (бот? → пропустить; освежаем chats.title)
    ▼
1. Знакомая идентичность (tg_identity, категория ≠ unknown)?
       └─ да → recap-приветствие (identity_greeting): имя из карточки выпускника /
               ручной правки / тг-ник + «о себе» + #whois. Без кика. Завершить.
2. Есть строка users (легаси, без идентичности)?
       └─ да → on_known_new_chat_member_message. Завершить.
3. Узнан как выпускник по нику/почте (find_by_username)?
       └─ да → alumni_whois_message (имя/класс + био + #whois),
               tg_identity(category=alumni). Без кика. Завершить.
4. Иначе → кнопочная анкета (whois.start) + таймеры кика/напоминания.
```

## Кнопочная анкета (`whois.py`)

State machine в `context.chat_data['whois'][user_id]`. На каждом шаге кнопка
**Назад**.

```
категория (🎓 выпускник · 📚 студент · 🤝 друг/сотрудник РЭШ)
   ├─ выпускник → e-mail → [совпал в директории → привязка, конец]
   │                       └─ нет → программа → год → анкета (текст)
   ├─ студент   → программа → год → анкета (текст)
   └─ друг/сотрудник → роль → анкета (текст)

Годы: выпускник — текущий и старше; студент — текущий + будущие
      (BAE +4, остальные +2), без шага десятилетий.
Текст анкеты у каждой категории свой; валидируется (check_freeform):
      длина ≥ min_whois_length И осмысленность (не «maf27 .....»).

Завершение (_finish_declared):
   ├─ classify() → tg_identity(source=buttons); свободный текст → intro
   │   (declared_name НЕ пишем — имя не выдумываем)
   ├─ удаляем промежуточные сообщения (промпты бота + ввод пользователя)
   ├─ отменяем таймеры
   └─ приветствие: кликабельное упоминание + %CLASS% + текст анкеты + #whois
```

Ввод email/имени приходит в `try_whois_text` (хук в начале `on_message`);
кнопки — в `on_whois_callback` (pattern `^w:`). При провале валидации мусор
удаляется, бот просит переписать, таймер продолжает идти.

## Таймеры кика

```
on_notify_timeout (за notify_delta мин. до кика)
   └─ notify_message («не закончили знакомство… через %MINUTES% мин. удалю»),
      удаляется автоматически к моменту кика

on_kick_timeout (kick_timeout мин.)
   ├─ удалить приветственное сообщение
   ├─ ban_chat_member(until_date = now + ban_duration; 0 = навсегда)
   └─ on_kick_message (если не «false»/«0»)
```

При `kick_timeout == 0` таймеры не ставятся (бот только приветствует).

## Выход участника (`on_chat_member_update`)

Ловится через **chat_member-апдейт** (работает и в group, и в supergroup, в
отличие от service-сообщения `left_chat_member`).

```
old.status ∈ {member,administrator,creator,restricted} → new ∈ {left,kicked}
   ├─ бот? → пропустить
   ├─ кикнул сам бот (from_user == бот)? → пропустить (есть on_kick_message)
   └─ иначе → on_left_chat_member_message (если не «false»/«0»)
```

## Настройка (`/start` в личке)

```
Бот добавлен в чат (on_my_chat_member) → создаётся строка chats (+ title)
   │
Админ пишет /start → список чатов из таблицы chats, где он админ
   │  (представляться через #whois НЕ нужно)
   ▼
Выбор чата → двухуровневое меню: 💬 Тексты · ⏱ Кик и тайминги · 🛡 Антиспам
   │  (open_texts/open_kick/open_filter)
   ▼
Кнопка настройки → authorize_user → показать текущее значение → «Отправьте новое»
   │  user_data[action]/[chat_id]
   ▼
Админ шлёт текст → on_message (DM) → authorize_user → сохранить в Chat
   (числа валидируются; шаблоны — как есть; regex: %TURN_OFF% отключает)
```

«Получить текущие настройки» рендерит все поля plain-text, шлёт несколькими
сообщениями (лимит 4096).

## Команды администратора

```
/skip  (ответ на сообщение) → cancel_kick_jobs, в БД НЕ пишет
/approve (ответ на сообщение/приветствие) → User в БД + cancel_kick_jobs
/whois @username|<id>|ответ → показать сохранённое представление (whois_text экранируется)
```

## Regex-фильтр

```
Любое сообщение (on_message) / форвард (on_forward)
   ├─ автор admin? / regex_filter пуст? → пропустить
   ├─ filter_only_new_users И пользователь уже известен? → пропустить
   └─ совпадение → удалить, cancel_kick_jobs, on_filtered_message, ban_chat_member
```

## Синхронизация директории (дашборд/CLI)

```
runner.run(new|full) → nes_scraper (login → index → download → parse → out/alumni.json)
   → nes_db.ingest (content-hash diff, история версий, программы/годы)
   → reconcile (перепривязка unresolved по нику/почте)

bootstrap.py = init_db + ingest(full) + seed(members.csv) + reconcile
   (одна идемпотентная команда для деплоя на Railway)
```
