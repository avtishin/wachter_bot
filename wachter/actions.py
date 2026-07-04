import json
import logging
import re
from datetime import datetime, timedelta
from time import monotonic

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from telegram.helpers import escape_markdown

from model import Chat, User, TgIdentity, AlumniPerson, session_scope
from constants import Actions
import constants
import alumni
import whois

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
# httpx/httpcore логируют каждый HTTP-запрос на уровне INFO, включая URL с токеном
# бота (например, getUpdates раз в ~10 сек при long-polling). Поднимаем порог до
# WARNING: шум уходит, токен в логи не попадает, реальные ошибки HTTP остаются видны.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# Счётчик попыток /whois от не-админов: user_id → (количество, время_последней_попытки)
# Запись удаляется если пользователь молчал дольше _WHOIS_TTL_SECONDS
_whois_nonadmin_attempts: dict[int, tuple[int, float]] = {}
_WHOIS_SPAM_THRESHOLD = 5
_WHOIS_TTL_SECONDS = 6 * 3600  # 6 часов бездействия — забыть
_WHOIS_SPAM_RESPONSES = [
    "Достал деда! 👴",
    "Дед сказал — не для тебя эта команда. 👴💢",
    "Ты серьёзно? ДЕД УСТАЛ. 😤",
    "Иди лучше #whois напиши, чем деда доставать! 👴🔥",
    "Всё. Дед на пенсии. Не беспокоить. 👴💤",
]


def _ban_until(ban_duration_minutes: int):
    """Возвращает until_date для ban_chat_member. 0 = бессрочный бан."""
    if ban_duration_minutes == 0:
        return None
    return datetime.now() + timedelta(minutes=ban_duration_minutes)


def apply_timeout(text, timeout):
    """Подставляет %TIMEOUT%. Если кик отключён (0) — убирает упоминание таймаута,
    чтобы не показывать «не установлен»."""
    if timeout and timeout > 0:
        return text.replace("%TIMEOUT%", f"{timeout} мин.")
    text = re.sub(r"\s*\([^()]*%TIMEOUT%[^()]*\)", "", text)       # ( … %TIMEOUT% … )
    text = re.sub(r"[^\n.!?]*%TIMEOUT%[^\n.!?]*[.!?]?", "", text)   # целая фраза
    return text.replace("%TIMEOUT%", "").strip()


# --- проверка прав бота в чате ---------------------------------------------
# Боту нужны права админа: блокировать участников (кик) и удалять сообщения
# (whois-ввод + чистка). Без них бот отказывается обрабатывать чат.
_RIGHTS_LABELS = {
    "admin": "сделать меня администратором",
    "ban": "право «Блокировка участников»",
    "delete": "право «Удаление сообщений»",
}
_rights_warned: set[int] = set()   # чаты, которым уже показали предупреждение


async def bot_missing_rights(bot, chat_id):
    """Список недостающих прав бота ([] — всё в порядке)."""
    try:
        me = await bot.get_chat_member(chat_id, bot.id)
    except Exception:
        return ["admin"]
    if getattr(me, "status", None) != "administrator":
        return ["admin"]
    missing = []
    if not getattr(me, "can_restrict_members", False):
        missing.append("ban")
    if not getattr(me, "can_delete_messages", False):
        missing.append("delete")
    return missing


async def ensure_rights(context, chat_id):
    """True если прав хватает. Иначе один раз предупреждает чат и возвращает False."""
    missing = await bot_missing_rights(context.bot, chat_id)
    if not missing:
        _rights_warned.discard(chat_id)
        return True
    if chat_id not in _rights_warned:
        _rights_warned.add(chat_id)
        need = ", ".join(_RIGHTS_LABELS[m] for m in missing)
        try:
            await context.bot.send_message(
                chat_id,
                f"⚠️ Мне не хватает прав, чтобы работать в этом чате: {need}. "
                f"Пока прав нет, я не приветствую и не проверяю участников.")
        except Exception:
            pass
    return False


async def on_my_chat_member(update, context: ContextTypes.DEFAULT_TYPE):
    """Реакция на изменение статуса бота в чате — сразу проверяем права."""
    chat = update.effective_chat
    if chat is None or chat.id >= 0:
        return
    # Бот добавлен/оставлен в чате → заводим строку конфига, чтобы админ мог
    # настроить бота через /start сразу, даже не написав ни одного #whois.
    cmu = update.my_chat_member
    status = cmu.new_chat_member.status if cmu and cmu.new_chat_member else None
    if status in ("member", "administrator", "restricted", "creator"):
        with session_scope() as sess:
            row = sess.query(Chat).filter(Chat.id == chat.id).first()
            if row is None:
                row = Chat(id=chat.id)
                sess.add(row)
            title = getattr(chat, "title", None)
            if isinstance(title, str) and title:
                row.title = title
    if await ensure_rights(context, chat.id):
        _rights_warned.discard(chat.id)


async def on_error(update, context: ContextTypes.DEFAULT_TYPE):
    logger.warning(f'Update "{update}" caused error "{context.error}"')


async def authorize_user(bot, chat_id, user_id):
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ["creator", "administrator"]
    except Exception:
        return False


def escape_md_v1(text):
    """Делает произвольный текст безопасным для legacy Markdown (v1).

    v1 не умеет экранировать ``]`` (в отличие от ``_ * [ ``` `` `` ```), поэтому
    ``]`` удаляем — тогда паттерн ссылки ``](`` собрать нельзя, а одиночные
    ``( )`` безвредны. Остальное экранирует escape_markdown. Закрывает инъекцию
    markdown/фишинг-ссылок через подконтрольный пользователю текст (имя, whois)."""
    return escape_markdown((text or "").replace("]", ""), version=1)


def safe_mention(user):
    """Кликабельное упоминание с экранированным именем (без markdown-инъекции)."""
    name = user.full_name or user.name or str(user.id)
    return f"[{escape_md_v1(name)}](tg://user?id={user.id})"


async def mention_markdown(bot, chat_id, user_id, message):
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        user = member.user
        user_mention_markdown = safe_mention(user) if user.name else ""
    except Exception:
        user_mention_markdown = ""
    return message.replace("%USER\\_MENTION%", user_mention_markdown)


async def cancel_kick_jobs(bot, job_queue, chat_id, user_id):
    """Отменяет все pending kick/notify джобы для пользователя в чате.
    Использует именованные джобы для O(1) поиска.
    Возвращает True если хоть один был отменён."""
    removed = False
    for name in [f"kick_{chat_id}_{user_id}", f"notify_{chat_id}_{user_id}"]:
        for job in job_queue.get_jobs_by_name(name):
            data = job.data or {}
            if "message_id" in data:
                try:
                    await bot.delete_message(data["chat_id"], data["message_id"])
                except Exception:
                    pass
            job.schedule_removal()
            removed = True
    return removed


async def on_help_command(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(constants.help_message)


async def on_skip_command(update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat_id = message.chat_id

    if chat_id > 0:
        return

    if message.reply_to_message is not None:
        if message.reply_to_message.from_user is None:
            await message.reply_text("Невозможно определить пользователя (анонимное сообщение).")
            return
        target_user_id = message.reply_to_message.from_user.id
        if message.from_user is None:
            await message.reply_text("Невозможно определить администратора (анонимное сообщение).")
            return
        issuer_user_id = message.from_user.id

        if not await authorize_user(context.bot, chat_id, issuer_user_id):
            await message.reply_text("Эта команда доступна только администраторам.")
            return

        removed = await cancel_kick_jobs(context.bot, context.job_queue, chat_id, target_user_id)
        if removed:
            await message.reply_text(constants.on_success_skip)
    else:
        # Подсказку показываем только администратору — не спамим чат для всех
        if message.from_user is not None and await authorize_user(context.bot, chat_id, message.from_user.id):
            await message.reply_text(constants.on_failed_skip)


async def on_new_chat_member(update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    # без прав админа (кик + удаление) не работаем — предупреждаем и выходим
    if not await ensure_rights(context, chat_id):
        return

    with session_scope() as sess:
        chat = sess.query(Chat).filter(Chat.id == chat_id).first()
        if chat is None:
            chat = Chat(id=chat_id)
            sess.add(chat)
            sess.flush()
        _title = getattr(update.effective_chat, "title", None)
        if isinstance(_title, str) and _title:   # держим название свежим для дашборда
            chat.title = _title

        known_message = chat.on_known_new_chat_member_message
        timeout = chat.kick_timeout
        notify_delta = chat.notify_delta
        # редактируемые шаблоны whois-флоу (fallback на константы для старых чатов)
        whois_templates = {
            "welcome": getattr(chat, "on_whois_welcome_message", None)
            or constants.whois_welcome_message,
            "alumni_welcome": getattr(chat, "on_alumni_welcome_message", None)
            or constants.on_alumni_welcome_message,
            "email_prompt": getattr(chat, "on_email_prompt_message", None)
            or constants.on_email_prompt_message,
            "name_prompt": getattr(chat, "on_whois_name_message", None)
            or constants.whois_ask_name_message,
            "student_prompt": getattr(chat, "on_student_prompt_message", None)
            or constants.on_student_prompt_message,
            "friend_prompt": getattr(chat, "on_friend_prompt_message", None)
            or constants.on_friend_prompt_message,
            "employee_prompt": getattr(chat, "on_employee_prompt_message", None)
            or constants.on_employee_prompt_message,
            "introduce": chat.on_introduce_message,
            "min_whois_length": chat.min_whois_length,
        }

    for member in update.message.new_chat_members:
        if member.is_bot:
            continue
        user_id = member.id

        await cancel_kick_jobs(context.bot, context.job_queue, chat_id, user_id)
        username = member.username

        # 1) известная идентичность (в т.ч. сид из members.csv) → recap, без кика
        recap = None
        with session_scope() as sess:
            ident = sess.get(TgIdentity, user_id)
            if ident is not None and ident.category and ident.category != "unknown":
                alum = sess.get(AlumniPerson, ident.alumni_uid) if ident.alumni_uid else None
                recap = alumni.identity_greeting(ident, alum, whois_templates["alumni_welcome"])
                if isinstance(username, str) and username.strip():
                    ident.username = alumni.normalize_username(username)
                sess.merge(User(chat_id=chat_id, user_id=user_id, whois="known"))
        if recap is not None:
            await update.message.reply_text(recap)
            continue

        # 2) известен по users (старые #whois без идентичности) → обычное приветствие
        with session_scope() as sess:
            user_found = sess.query(User).filter(
                User.chat_id == chat_id, User.user_id == user_id).first() is not None
        logger.info(f"on_new_chat_member: chat_id={chat_id} user_id={user_id} found_in_db={user_found}")
        if user_found:
            await update.message.reply_text(known_message)
            continue

        # 3) узнаём выпускника по telegram-нику (member.username: str | None)
        welcome = None
        if isinstance(username, str) and username.strip():
            with session_scope() as sess:
                alum = alumni.find_by_username(sess, username)
                if alum is not None:
                    welcome = alumni.alumni_whois_message(whois_templates["alumni_welcome"], alum)
                    alumni.upsert_identity(sess, user_id, username=username,
                                           category="alumni", alumni_uid=alum.uid,
                                           source="join")
                    sess.merge(User(chat_id=chat_id, user_id=user_id,
                                    whois=f"alumni:{alum.uid}"))
        if welcome is not None:
            await update.message.reply_text(welcome)
            continue

        # структурированный whois на кнопках: тёплое приветствие + категории
        welcome_text = apply_timeout(whois_templates["welcome"], timeout)
        welcome_text = await mention_markdown(context.bot, chat_id, user_id, welcome_text)
        msg = await whois.start(update, context, chat_id, user_id, username,
                                welcome_text, whois_templates)

        if timeout != 0:
            if notify_delta > 0 and timeout > notify_delta:
                context.job_queue.run_once(
                    on_notify_timeout,
                    (timeout - notify_delta) * 60,
                    data={"chat_id": chat_id, "user_id": user_id},
                    name=f"notify_{chat_id}_{user_id}",
                )
            context.job_queue.run_once(
                on_kick_timeout,
                timeout * 60,
                data={"chat_id": chat_id, "user_id": user_id, "message_id": msg.message_id},
                name=f"kick_{chat_id}_{user_id}",
            )


async def on_notify_timeout(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    with session_scope() as sess:
        chat = sess.query(Chat).filter(Chat.id == data["chat_id"]).first()
        if chat is None:
            return
        notify_delta = chat.notify_delta
        msg_markdown = await mention_markdown(
            context.bot, data["chat_id"], data["user_id"], chat.notify_message
        )
    # %MINUTES% — сколько минут осталось до кика (напоминание шлётся за notify_delta до него)
    msg_markdown = msg_markdown.replace("%MINUTES%", str(notify_delta))
    message = await context.bot.send_message(
        data["chat_id"], text=msg_markdown, parse_mode=ParseMode.MARKDOWN
    )
    context.job_queue.run_once(
        delete_message,
        notify_delta * 60,
        data={"chat_id": data["chat_id"], "message_id": message.message_id},
    )


async def delete_message(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    try:
        await context.bot.delete_message(data["chat_id"], data["message_id"])
    except Exception:
        logger.warning(f"can't delete {data['message_id']} from {data['chat_id']}")


async def on_kick_timeout(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    try:
        await context.bot.delete_message(data["chat_id"], data["message_id"])
    except Exception:
        pass

    try:
        with session_scope() as sess:
            chat = sess.query(Chat).filter(Chat.id == data["chat_id"]).first()
            ban_duration = chat.ban_duration
            kick_msg = chat.on_kick_message

        await context.bot.ban_chat_member(
            data["chat_id"],
            data["user_id"],
            until_date=_ban_until(ban_duration),
        )
        if kick_msg.lower() not in ["false", "0"]:
            msg_markdown = await mention_markdown(
                context.bot, data["chat_id"], data["user_id"], kick_msg
            )
            await context.bot.send_message(
                data["chat_id"], text=msg_markdown, parse_mode=ParseMode.MARKDOWN
            )
    except Exception as e:
        logger.error(e)
        await context.bot.send_message(data["chat_id"], text=constants.on_failed_kick_response)


async def on_approve_command(update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat_id = message.chat_id

    if chat_id > 0:
        return

    if message.from_user is None:
        await message.reply_text("Невозможно определить администратора (анонимное сообщение).")
        return

    if not await authorize_user(context.bot, chat_id, message.from_user.id):
        await message.reply_text("Эта команда доступна только администраторам.")
        return

    if message.reply_to_message is None:
        await message.reply_text("Ответьте на сообщение пользователя, которого нужно одобрить.")
        return

    reply_from = message.reply_to_message.from_user

    if reply_from is not None and not reply_from.is_bot:
        # Ответ на сообщение самого пользователя
        target_user_id = reply_from.id
    else:
        # Ответ на сообщение бота — ищем упоминание пользователя в entities
        target_user_id = None
        for entity, _ in (message.reply_to_message.parse_entities(["text_mention"]).items()):
            target_user_id = entity.user.id
            break
        if target_user_id is None:
            await message.reply_text(
                "Не удалось определить пользователя. Ответьте на сообщение самого пользователя."
            )
            return

    with session_scope() as sess:
        sess.merge(User(chat_id=chat_id, user_id=target_user_id, whois="Одобрен администратором"))

    await cancel_kick_jobs(context.bot, context.job_queue, chat_id, target_user_id)
    await message.reply_text("Пользователь одобрен.")


async def _process_whois(bot, job_queue, message, chat_id, user_id):
    """Сохраняет whois и отменяет кик. Возвращает True если кик был активен.
    Не перезаписывает whois, если пользователь уже в БД и нет активного kick-джоба
    (защита от случайной перезаписи при упоминании тега в разговоре)."""
    has_kick_job = bool(job_queue.get_jobs_by_name(f"kick_{chat_id}_{user_id}"))

    with session_scope() as sess:
        chat = sess.query(Chat).filter(Chat.id == chat_id).first()
        if chat is None:
            chat = Chat(id=chat_id)
            sess.add(chat)
            sess.flush()
        introduce_message = chat.on_introduce_message

        existing_user = sess.query(User).filter(
            User.chat_id == chat_id, User.user_id == user_id
        ).first()

        # Перезаписываем только если: пользователь новый ИЛИ есть активный kick-джоб
        if existing_user is None or has_kick_job:
            sess.merge(User(chat_id=chat_id, user_id=user_id, whois=message.text))
        else:
            return False

    removed = await cancel_kick_jobs(bot, job_queue, chat_id, user_id)
    if removed:
        msg_markdown = await mention_markdown(bot, chat_id, user_id, introduce_message)
        await message.reply_text(msg_markdown, parse_mode=ParseMode.MARKDOWN)
    return removed


async def on_hashtag_message(update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat_id = message.chat_id

    has_whois = "#whois" in message.parse_entities(types=["hashtag"]).values()
    if has_whois and chat_id < 0 and message.from_user is not None:
        with session_scope() as sess:
            chat = sess.query(Chat).filter(Chat.id == chat_id).first()
            min_len = chat.min_whois_length if chat else 20
        if len(message.text or "") >= min_len:
            await _process_whois(context.bot, context.job_queue, message, chat_id, message.from_user.id)
            return
    await on_message(update, context)


async def on_edited_message(update, context: ContextTypes.DEFAULT_TYPE):
    """Принимает #whois из отредактированного сообщения."""
    message = update.edited_message
    if message is None or message.chat_id >= 0:
        return
    if message.from_user is None:
        return
    if "#whois" not in message.parse_entities(types=["hashtag"]).values():
        return
    chat_id = message.chat_id
    with session_scope() as sess:
        chat = sess.query(Chat).filter(Chat.id == chat_id).first()
        min_len = chat.min_whois_length if chat else 20
    if len(message.text or "") >= min_len:
        await _process_whois(context.bot, context.job_queue, message, chat_id, message.from_user.id)


async def admin_chats_keyboard(bot, user_id):
    """Кнопки чатов, которыми бот управляет (строки в `chats`) и где user —
    админ/создатель. Источник — таблица `chats`, а не `users`: настраивать бота
    может любой Telegram-админ чата, даже если он сам не писал #whois. Покинутые
    чаты отсеиваются сами — там get_chat_member бросит исключение."""
    with session_scope() as sess:
        chat_ids = [row[0] for row in sess.query(Chat.id).all()]

    keyboard = []
    for chat_id in chat_ids:
        try:
            if await authorize_user(bot, chat_id, user_id):
                chat = await bot.get_chat(chat_id)
                title = chat.title or str(chat_id)
                keyboard.append([InlineKeyboardButton(
                    title,
                    callback_data=json.dumps({"chat_id": chat_id, "action": Actions.select_chat}),
                )])
        except Exception:
            pass
    return keyboard


async def on_start_command(update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if update.effective_chat.id < 0:
        return

    keyboard = await admin_chats_keyboard(context.bot, user_id)

    if not keyboard:
        await update.message.reply_text("У вас нет доступных чатов.")
        return

    await update.message.reply_text(
        constants.on_start_command,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# Редактируемое действие → колонка Chat (для показа текущего значения и записи).
ACTION_FIELD = {
    Actions.set_kick_timeout: "kick_timeout",
    Actions.set_notify_delta: "notify_delta",
    Actions.set_ban_duration: "ban_duration",
    Actions.set_min_whois_length: "min_whois_length",
    Actions.set_on_whois_welcome_message: "on_whois_welcome_message",
    Actions.set_on_alumni_welcome_message: "on_alumni_welcome_message",
    Actions.set_on_email_prompt_message: "on_email_prompt_message",
    Actions.set_on_whois_name_message: "on_whois_name_message",
    Actions.set_on_student_prompt_message: "on_student_prompt_message",
    Actions.set_on_friend_prompt_message: "on_friend_prompt_message",
    Actions.set_on_employee_prompt_message: "on_employee_prompt_message",
    Actions.set_on_successful_introducion_response: "on_introduce_message",
    Actions.set_on_known_new_chat_member_message_response: "on_known_new_chat_member_message",
    Actions.set_on_whois_reminder_message: "on_whois_reminder_message",
    Actions.set_notify_message: "notify_message",
    Actions.set_on_kick_message: "on_kick_message",
    Actions.set_on_left_chat_member_message: "on_left_chat_member_message",
    Actions.set_regex_filter: "regex_filter",
    Actions.set_filter_only_new_users: "filter_only_new_users",
    Actions.set_on_filtered_message: "on_filtered_message",
}


def split_message(text, limit=3900):
    """Режет длинный текст на части ≤ limit по границам строк (у Telegram лимит
    сообщения 4096). Всегда возвращает минимум одну часть."""
    chunks, cur = [], ""
    for line in text.split("\n"):
        piece = (line + "\n")
        if cur and len(cur) + len(piece) > limit:
            chunks.append(cur.rstrip("\n"))
            cur = ""
        cur += piece
    chunks.append(cur.rstrip("\n"))
    return chunks


def _unescape_md(s):
    """Убирает markdown-экранирование для показа плейсхолдеров админу:
    \\_ -> _, \\# -> #, \\* -> *, \\[ -> [."""
    for a, b in (("\\_", "_"), ("\\#", "#"), ("\\*", "*"), ("\\[", "[")):
        s = s.replace(a, b)
    return s


def _btn(label, chat_id, action):
    return [InlineKeyboardButton(
        label, callback_data=json.dumps({"chat_id": chat_id, "action": action}))]


def _chat_menu_keyboard(chat_id):
    return InlineKeyboardMarkup([
        _btn("💬 Тексты сообщений", chat_id, Actions.open_texts),
        _btn("⏱ Кик и тайминги", chat_id, Actions.open_kick),
        _btn("🛡 Антиспам-фильтр", chat_id, Actions.open_filter),
        _btn("📋 Текущие настройки", chat_id, Actions.get_current_settings),
        [InlineKeyboardButton("◀ К списку чатов", callback_data=json.dumps(
            {"action": Actions.start_select_chat}))],
    ])


def _texts_keyboard(chat_id):
    return InlineKeyboardMarkup([
        _btn("Приветствие-знакомство (whois)", chat_id, Actions.set_on_whois_welcome_message),
        _btn("Приветствие выпускника", chat_id, Actions.set_on_alumni_welcome_message),
        _btn("Запрос e-mail (выпускник)", chat_id, Actions.set_on_email_prompt_message),
        _btn("Анкета: выпускник", chat_id, Actions.set_on_whois_name_message),
        _btn("Анкета: студент", chat_id, Actions.set_on_student_prompt_message),
        _btn("Анкета: друг РЭШ", chat_id, Actions.set_on_friend_prompt_message),
        _btn("Анкета: сотрудник РЭШ", chat_id, Actions.set_on_employee_prompt_message),
        _btn("Сообщение после знакомства", chat_id, Actions.set_on_successful_introducion_response),
        _btn("Сообщение при перезаходе", chat_id, Actions.set_on_known_new_chat_member_message_response),
        _btn("Напоминание написать #whois", chat_id, Actions.set_on_whois_reminder_message),
        _btn("Предупреждение перед киком", chat_id, Actions.set_notify_message),
        _btn("Сообщение после кика", chat_id, Actions.set_on_kick_message),
        _btn("Сообщение при выходе из чата", chat_id, Actions.set_on_left_chat_member_message),
        _btn("◀ Назад", chat_id, Actions.select_chat),
    ])


def _kick_keyboard(chat_id):
    return InlineKeyboardMarkup([
        _btn("Таймаут кика", chat_id, Actions.set_kick_timeout),
        _btn("Напоминание (мин. до кика)", chat_id, Actions.set_notify_delta),
        _btn("Длительность бана (мин.)", chat_id, Actions.set_ban_duration),
        _btn("Мин. длина #whois", chat_id, Actions.set_min_whois_length),
        _btn("◀ Назад", chat_id, Actions.select_chat),
    ])


def _filter_keyboard(chat_id):
    return InlineKeyboardMarkup([
        _btn("Regex для фильтра сообщений", chat_id, Actions.set_regex_filter),
        _btn("Фильтрация только для новых", chat_id, Actions.set_filter_only_new_users),
        _btn("Сообщение при бане (regex)", chat_id, Actions.set_on_filtered_message),
        _btn("◀ Назад", chat_id, Actions.select_chat),
    ])


async def on_button_click(update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    try:
        data = json.loads(query.data)
    except (json.JSONDecodeError, TypeError):
        await query.answer(text="Устаревшая кнопка — откройте меню заново через /start.", show_alert=True)
        return
    if "action" not in data:
        await query.answer()
        return

    # Для всех действий с конкретным чатом проверяем права перед подтверждением
    if "chat_id" in data and not await authorize_user(context.bot, data["chat_id"], user_id):
        await query.answer(text="Недостаточно прав.", show_alert=True)
        return

    await query.answer()

    if data["action"] == Actions.start_select_chat:
        keyboard = await admin_chats_keyboard(context.bot, user_id)

        if not keyboard:
            await query.edit_message_text("У вас нет доступных чатов.")
            return

        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))

    elif data["action"] == Actions.select_chat:
        await query.edit_message_reply_markup(reply_markup=_chat_menu_keyboard(data["chat_id"]))

    elif data["action"] == Actions.open_texts:
        await query.edit_message_reply_markup(reply_markup=_texts_keyboard(data["chat_id"]))

    elif data["action"] == Actions.open_kick:
        await query.edit_message_reply_markup(reply_markup=_kick_keyboard(data["chat_id"]))

    elif data["action"] == Actions.open_filter:
        await query.edit_message_reply_markup(reply_markup=_filter_keyboard(data["chat_id"]))

    elif data["action"] in ACTION_FIELD:
        # показываем текущее значение перед вводом нового
        field = ACTION_FIELD[data["action"]]
        with session_scope() as sess:
            chat = sess.query(Chat).filter(Chat.id == data["chat_id"]).first()
            current = getattr(chat, field, None) if chat else None
        current_str = "—" if current in (None, "") else str(current)
        await query.edit_message_text(
            f"Текущее значение:\n\n{current_str}\n\nОтправьте новое значение:")
        context.user_data["chat_id"] = data["chat_id"]
        context.user_data["action"] = data["action"]

    elif data["action"] == Actions.get_current_settings:
        keyboard = [[
            InlineKeyboardButton("К настройке чата", callback_data=json.dumps(
                {"chat_id": data["chat_id"], "action": Actions.select_chat})),
            InlineKeyboardButton("К списку чатов", callback_data=json.dumps(
                {"action": Actions.start_select_chat})),
        ]]
        with session_scope() as sess:
            chat = sess.query(Chat).filter(Chat.id == data["chat_id"]).first()
            if chat is None:
                chat = Chat(id=data["chat_id"])
                sess.add(chat)
                sess.flush()
            # plain text (без parse_mode): устойчиво к любым правкам админов и
            # показываем плейсхолдеры без markdown-экранирования (\_ -> _)
            values = {k: (_unescape_md(v) if isinstance(v, str) else v)
                      for k, v in chat.__dict__.items() if not k.startswith("_")}
            text = constants.get_settings_message.format(**values)
        # шлём несколькими сообщениями — общий текст может превысить лимит 4096
        chunks = split_message(text)
        await query.edit_message_text(
            text=chunks[0],
            reply_markup=None if len(chunks) > 1 else InlineKeyboardMarkup(keyboard))
        for extra in chunks[1:-1]:
            await context.bot.send_message(query.message.chat_id, extra)
        if len(chunks) > 1:
            await context.bot.send_message(
                query.message.chat_id, chunks[-1],
                reply_markup=InlineKeyboardMarkup(keyboard))
        context.user_data["action"] = None


def filter_message(chat_id, message_text):
    if not message_text:
        return False
    with session_scope() as sess:
        chat = sess.query(Chat).filter(Chat.id == chat_id).first()
        if chat is None or chat.regex_filter is None:
            return False
        try:
            return re.search(chat.regex_filter, message_text)
        except re.error:
            logger.warning(f"Invalid regex filter for chat {chat_id}: {chat.regex_filter!r}")
            return False


async def on_forward(update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat_id = message.chat_id

    if message.from_user is None or chat_id > 0:
        return
    user_id = message.from_user.id

    if await authorize_user(context.bot, chat_id, user_id):
        return

    with session_scope() as sess:
        chat = sess.query(Chat).filter(Chat.id == chat_id).first()
        if chat is None or chat.regex_filter is None:
            return
        if chat.filter_only_new_users and not is_new_user(chat_id, user_id):
            return
        filtered_msg = chat.on_filtered_message
        ban_duration = chat.ban_duration

    await cancel_kick_jobs(context.bot, context.job_queue, chat_id, user_id)
    await context.bot.delete_message(chat_id, message.message_id)
    msg_markdown = await mention_markdown(context.bot, chat_id, user_id, filtered_msg)
    await context.bot.send_message(chat_id, text=msg_markdown, parse_mode=ParseMode.MARKDOWN)
    await context.bot.ban_chat_member(
        chat_id, user_id, until_date=_ban_until(ban_duration)
    )


def is_new_user(chat_id, user_id):
    with session_scope() as sess:
        user = sess.query(User).filter(User.user_id == user_id, User.chat_id == chat_id).first()
        return not user


def is_chat_filters_new_users(chat_id):
    with session_scope() as sess:
        return bool(sess.query(Chat.filter_only_new_users).filter(Chat.id == chat_id).scalar())


async def on_message(update, context: ContextTypes.DEFAULT_TYPE):
    # kнопочный whois ждёт email/имя от новичка — перехватываем до фильтров
    if await whois.try_whois_text(update, context):
        return
    message = update.effective_message
    chat_id = message.chat_id

    if chat_id < 0:
        if message.from_user is None:
            return
        user_id = message.from_user.id
        message_text = message.text or message.caption
        filter_mask = (
            not await authorize_user(context.bot, chat_id, user_id)
            and filter_message(chat_id, message_text)
        )
        if filter_mask and is_chat_filters_new_users(chat_id):
            filter_mask = is_new_user(chat_id, user_id)

        if not filter_mask and not (message.text or "").startswith("/"):
            kick_jobs = context.job_queue.get_jobs_by_name(f"kick_{chat_id}_{user_id}")
            if kick_jobs:
                with session_scope() as sess:
                    chat = sess.query(Chat).filter(Chat.id == chat_id).first()
                    reminder_template = chat.on_whois_reminder_message if chat else None
                    min_len = str(chat.min_whois_length) if chat else "20"
                if reminder_template:
                    reminder = await mention_markdown(context.bot, chat_id, user_id, reminder_template)
                    reminder = reminder.replace("%MIN\\_LENGTH%", min_len).replace("%MIN_LENGTH%", min_len)
                    await message.reply_text(reminder, parse_mode=ParseMode.MARKDOWN)

        if filter_mask:
            with session_scope() as sess:
                chat = sess.query(Chat).filter(Chat.id == chat_id).first()
                filtered_msg = chat.on_filtered_message if chat else ""
                ban_duration = chat.ban_duration if chat else 1
            await context.bot.delete_message(chat_id, message.message_id)
            msg_markdown = await mention_markdown(context.bot, chat_id, user_id, filtered_msg)
            await cancel_kick_jobs(context.bot, context.job_queue, chat_id, user_id)
            await context.bot.send_message(chat_id, text=msg_markdown, parse_mode=ParseMode.MARKDOWN)
            await context.bot.ban_chat_member(
                chat_id, user_id, until_date=_ban_until(ban_duration)
            )
    else:
        user_id = chat_id
        action = context.user_data.get("action")

        if action is None:
            return

        chat_id = context.user_data.get("chat_id")
        if chat_id is None:
            context.user_data["action"] = None
            return

        if not await authorize_user(context.bot, chat_id, user_id):
            await message.reply_text("У вас нет прав для изменения настроек этого чата.")
            context.user_data["action"] = None
            return

        numeric_saved = False

        if action == Actions.set_kick_timeout:
            try:
                timeout = int(message.text)
                assert timeout >= 0
            except Exception:
                await message.reply_text(constants.on_failed_set_kick_timeout_response)
                return
            with session_scope() as sess:
                sess.merge(Chat(id=chat_id, kick_timeout=timeout))
            context.user_data["action"] = None
            numeric_saved = True

        elif action == Actions.set_notify_delta:
            try:
                delta = int(message.text)
                assert delta >= 0
            except Exception:
                await message.reply_text("Введите целое неотрицательное число (минут до кика для напоминания, 0 — отключить).")
                return
            with session_scope() as sess:
                sess.merge(Chat(id=chat_id, notify_delta=delta))
            context.user_data["action"] = None
            numeric_saved = True

        elif action == Actions.set_min_whois_length:
            try:
                length = int(message.text)
                assert length > 0
            except Exception:
                await message.reply_text("Введите целое положительное число (минимальная длина #whois сообщения).")
                return
            with session_scope() as sess:
                sess.merge(Chat(id=chat_id, min_whois_length=length))
            context.user_data["action"] = None
            numeric_saved = True

        elif action == Actions.set_ban_duration:
            try:
                duration = int(message.text)
                assert duration >= 0
            except Exception:
                await message.reply_text("Введите целое неотрицательное число в минутах (0 — бессрочный бан).")
                return
            with session_scope() as sess:
                sess.merge(Chat(id=chat_id, ban_duration=duration))
            context.user_data["action"] = None
            numeric_saved = True

        if numeric_saved:
            keyboard = [[
                InlineKeyboardButton("К настройке чата", callback_data=json.dumps(
                    {"chat_id": chat_id, "action": Actions.select_chat})),
                InlineKeyboardButton("К списку чатов", callback_data=json.dumps(
                    {"action": Actions.start_select_chat})),
            ]]
            await message.reply_text(
                constants.on_set_new_message,
                reply_markup=InlineKeyboardMarkup(keyboard),
            )

        elif action in ACTION_FIELD:
            value = message.text_markdown
            field = ACTION_FIELD[action]
            with session_scope() as sess:
                if action == Actions.set_filter_only_new_users:
                    chat = Chat(id=chat_id, filter_only_new_users=value.lower() in ["true", "1"])
                elif action == Actions.set_regex_filter:
                    chat = Chat(id=chat_id, regex_filter=None if value == "%TURN_OFF%" else message.text)
                else:
                    chat = Chat(id=chat_id, **{field: value})
                sess.merge(chat)

            context.user_data["action"] = None

            keyboard = [[
                InlineKeyboardButton("К настройке чата", callback_data=json.dumps(
                    {"chat_id": chat_id, "action": Actions.select_chat})),
                InlineKeyboardButton("К списку чатов", callback_data=json.dumps(
                    {"action": Actions.start_select_chat})),
            ]]
            await message.reply_text(
                constants.on_set_new_message,
                reply_markup=InlineKeyboardMarkup(keyboard),
            )


async def on_whois_command(update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    message = update.effective_message

    if chat_id > 0:
        return

    if message.from_user is None or not await authorize_user(context.bot, chat_id, message.from_user.id):
        uid = message.from_user.id if message.from_user else 0
        now = monotonic()

        prev_count, last_time = _whois_nonadmin_attempts.get(uid, (0, now))
        # Если молчал дольше TTL — сбрасываем счётчик как для нового человека
        if now - last_time > _WHOIS_TTL_SECONDS:
            prev_count = 0

        count = prev_count + 1
        _whois_nonadmin_attempts[uid] = (count, now)

        if count % _WHOIS_SPAM_THRESHOLD == 0:
            idx = (count // _WHOIS_SPAM_THRESHOLD - 1) % len(_WHOIS_SPAM_RESPONSES)
            await message.reply_text(_WHOIS_SPAM_RESPONSES[idx])
        return

    # Определяем user_id из аргумента или reply
    user_id = None
    if context.args:
        arg = context.args[0]
        try:
            user_id = int(arg)
        except ValueError:
            # Telegram Bot API getChatMember принимает user_id только как целое число.
            # Поиск по @username невозможен через этот метод.
            await message.reply_text(
                "Укажите числовой ID пользователя или ответьте на его сообщение.\n"
                "Найти ID можно через @userinfobot."
            )
            return
    elif message.reply_to_message and message.reply_to_message.from_user:
        user_id = message.reply_to_message.from_user.id
    else:
        await message.reply_text("Usage: /whois <user_id> | ответ на сообщение")
        return

    with session_scope() as sess:
        user = sess.query(User).filter(
            User.chat_id == chat_id, User.user_id == user_id
        ).first()

        if user is None:
            await message.reply_text("Пользователь не найден в базе.")
            return

        whois_text = user.whois

    mention = await mention_markdown(context.bot, chat_id, user_id, "%USER\\_MENTION%")
    # whois_text — свободный текст пользователя: экранируем для markdown
    await message.reply_text(
        f"{mention}\nwhois: {escape_md_v1(whois_text)}",
        parse_mode=ParseMode.MARKDOWN,
    )


_PRESENT = ("member", "administrator", "creator", "restricted")


async def on_chat_member_update(update, context: ContextTypes.DEFAULT_TYPE):
    """Выход участника через chat_member-апдейт (работает и в group, и в
    supergroup, в отличие от service-сообщения left_chat_member, которого в
    супергруппах при добровольном выходе просто нет).

    Постим «покинул чат», когда участник был в чате, а стал left/kicked. Кик
    самим ботом пропускаем — про него уже есть on_kick_message."""
    cmu = update.chat_member
    if cmu is None:
        return
    old, new = cmu.old_chat_member, cmu.new_chat_member
    was_in = old.status in _PRESENT
    now_gone = new.status in ("left", "kicked")
    if not (was_in and now_gone):
        return
    member = new.user
    if member.is_bot:
        return
    # бот сам кикнул (таймер/regex) — не дублируем, есть отдельное сообщение
    if cmu.from_user is not None and cmu.from_user.id == context.bot.id:
        return
    chat_id = update.effective_chat.id
    with session_scope() as sess:
        chat = sess.query(Chat).filter(Chat.id == chat_id).first()
        if chat is None:
            return
        template = chat.on_left_chat_member_message
    if template.lower() in ["false", "0"]:
        return
    user_mention = safe_mention(member) if member.name else str(member.id)
    msg = template.replace("%USER\\_MENTION%", user_mention)
    await context.bot.send_message(chat_id, msg, parse_mode=ParseMode.MARKDOWN)
