from enum import IntEnum, auto

# MESSAGES
on_set_new_message = 'Обновил сообщение.'
on_success_set_kick_timeout_response = 'Обновил таймаут кика.'
on_failed_set_kick_timeout_response = 'Таймаут должен быть целым положительным числом'
on_failed_kick_response = 'Пользователь самостоятельно покинул чат.'
on_failed_skip = 'Ответьте на сообщение пользователя которого не нужно кикать'
on_success_skip = 'Теперь пользователю не нужно представляться.'
on_success_kick_response = r"%USER\_MENTION% не представился и был кикнут из чата."
on_start_command = 'Выберите чат и действие:'
skip_on_new_chat_member_message = "%SKIP%"
help_message = '''Привет. Для начала работы добавь меня в чат.
Для настройки бота админу нужно представиться в чате (написать сообщение с #whois длинной больше 20 символов) и написать мне в личных сообщениях /start.
По умолчанию я не кикаю непредставившихся, а лишь записываю все сообщения с тегом #whois.
Если нужно кикать, то установи таймаут кика в значение больше нуля (в минутах).
За настраиваемое время до кика я отправляю сообщение с напоминанием.

Команды для администраторов (в ответ на сообщение пользователя):
/skip — отменить кик для пользователя в этой сессии
/approve — одобрить пользователя навсегда (не потребует представления при повторном входе)

В шаблонах сообщений доступны плейсхолдеры:
%USER_MENTION% — упоминание пользователя
%TIMEOUT% — таймаут кика в минутах
%MINUTES% — в сообщении-напоминании: минут до кика
'''

get_settings_message = """⏱ ТАЙМИНГИ
Таймаут кика: {kick_timeout} мин.
Напоминание за: {notify_delta} мин. до кика
Длительность бана: {ban_duration} мин. (0 = навсегда)
Мин. длина #whois: {min_whois_length} символов

━━━━━━━━━━━━━
💬 ТЕКСТЫ

Приветствие-знакомство:
{on_whois_welcome_message}
———
Приветствие выпускника:
{on_alumni_welcome_message}
———
Запрос e-mail:
{on_email_prompt_message}
———
Анкета выпускника:
{on_whois_name_message}
———
Анкета студента:
{on_student_prompt_message}
———
Анкета друга РЭШ:
{on_friend_prompt_message}
———
Анкета сотрудника РЭШ:
{on_employee_prompt_message}
———
После знакомства:
{on_introduce_message}
———
При перезаходе:
{on_known_new_chat_member_message}
———
Напоминание написать #whois:
{on_whois_reminder_message}
———
Предупреждение перед киком:
{notify_message}
———
После кика:
{on_kick_message}
———
При выходе из чата:
{on_left_chat_member_message}

━━━━━━━━━━━━━
🛡 АНТИСПАМ
Regex фильтр: {regex_filter}
Кикать по regex только новых: {filter_only_new_users}
———
Сообщение при бане:
{on_filtered_message}
"""


default_kick_timeout = 0

# Приветствие найденного в базе выпускника. Плейсхолдеры: %NAME% %FIRST_NAME%
# %LAST_NAME% %CLASS% %PROGRAM%.
on_alumni_welcome_message = 'Добро пожаловать в Мишпуху 2.0, %NAME% (%CLASS%)! 🤍'

# Тёплое приветствие в начале кнопочного whois (для ненайденных). %USER_MENTION%.
whois_welcome_message = (
    'Привет, %USER\\_MENTION%.\n'
    'Рады видеть тебя в Мишпухе 2.0 🤍\n\n'
    'Это чат студентов, выпускников, сотрудников и друзей РЭШ.\n'
    'Давайте познакомимся — выберите, кто вы:'
)

# Запрос почты на ветке «Я выпускник» кнопочного whois.
on_email_prompt_message = (
    '📧 Введите ваш основной e-mail, указанный в профиле на my.nes.ru '
    '(раздел «Контактная информация»). Сверим его с директорией — если найдём, '
    'сразу вас узнаем. Не помните или не совпадёт — ничего страшного, '
    'продолжим вручную.'
)
whois_prompt_message = 'Давайте познакомимся. Кто вы?'
# Анкета выпускника (ручной ввод, если не нашли по нику/почте).
whois_ask_name_message = (
    'Напишите, пожалуйста, ваши Фамилию и Имя, а также пару слов о себе: '
    'где вы сейчас живёте и работаете, чем занимаетесь и в чём ваша экспертиза.'
)
# Анкета студента — акцент на до-РЭШ и жизни вне учёбы.
on_student_prompt_message = (
    'Напишите, пожалуйста, ваши Фамилию и Имя и пару слов о себе: '
    'что окончили или чем занимались до РЭШ, где сейчас живёте и '
    'чем увлекаетесь во внеучебное время.'
)
# Анкета друга РЭШ — попросить явно указать связь с РЭШ.
on_friend_prompt_message = (
    'Напишите, пожалуйста, ваши Фамилию и Имя и пару слов о себе: '
    'что связывает вас с РЭШ и чем вы занимаетесь.'
)
# Анкета сотрудника РЭШ — попросить указать роль в РЭШ.
on_employee_prompt_message = (
    'Напишите, пожалуйста, ваши Фамилию и Имя и пару слов о себе: '
    'чем вы занимаетесь в РЭШ.'
)


# ACTIONS
class Actions(IntEnum):
    start_select_chat = auto()
    select_chat = auto()
    set_on_new_chat_member_message_response = auto()
    set_notify_message = auto()
    set_on_successful_introducion_response = auto()
    set_on_known_new_chat_member_message_response = auto()
    set_kick_timeout = auto()
    set_on_kick_message = auto()
    set_on_left_chat_member_message = auto()
    set_notify_delta = auto()
    set_on_whois_reminder_message = auto()
    set_on_filtered_message = auto()
    set_min_whois_length = auto()
    set_ban_duration = auto()
    set_regex_filter = auto()
    set_filter_only_new_users = auto()
    get_current_settings = auto()
    # добавлены в конец, чтобы не сдвигать значения существующих
    set_on_alumni_welcome_message = auto()
    set_on_email_prompt_message = auto()
    set_on_whois_welcome_message = auto()
    set_on_whois_name_message = auto()
    # подменю настроек (двухуровневое меню)
    open_texts = auto()
    open_kick = auto()
    open_filter = auto()
    # категорийные анкеты кнопочного whois
    set_on_student_prompt_message = auto()
    set_on_friend_prompt_message = auto()
    set_on_employee_prompt_message = auto()


