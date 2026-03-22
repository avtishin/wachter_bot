import json
import logging
import re
from datetime import datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from model import Chat, User, session_scope
from constants import Actions
import constants

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def _ban_until(ban_duration_minutes: int):
    """Возвращает until_date для ban_chat_member. 0 = бессрочный бан."""
    if ban_duration_minutes == 0:
        return None
    return datetime.now() + timedelta(minutes=ban_duration_minutes)


async def on_error(update, context: ContextTypes.DEFAULT_TYPE):
    logger.warning(f'Update "{update}" caused error "{context.error}"')


async def authorize_user(bot, chat_id, user_id):
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ["creator", "administrator"]
    except Exception:
        return False


async def mention_markdown(bot, chat_id, user_id, message):
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        user = member.user
        user_mention_markdown = user.mention_markdown() if user.name else ""
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
        issuer_user_id = message.from_user.id

        if not await authorize_user(context.bot, chat_id, issuer_user_id):
            await message.reply_text("Эта команда доступна только администраторам.")
            return

        removed = await cancel_kick_jobs(context.bot, context.job_queue, chat_id, target_user_id)
        if removed:
            await message.reply_text(constants.on_success_skip)
    else:
        await message.reply_text(constants.on_failed_skip)


async def on_new_chat_member(update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    with session_scope() as sess:
        chat = sess.query(Chat).filter(Chat.id == chat_id).first()
        if chat is None:
            chat = Chat(id=chat_id)
            sess.add(chat)
            sess.flush()

        message_text = chat.on_new_chat_member_message
        known_message = chat.on_known_new_chat_member_message
        timeout = chat.kick_timeout
        notify_delta = chat.notify_delta

    for member in update.message.new_chat_members:
        if member.is_bot:
            continue
        user_id = member.id

        await cancel_kick_jobs(context.bot, context.job_queue, chat_id, user_id)

        with session_scope() as sess:
            user = sess.query(User).filter(
                User.chat_id == chat_id, User.user_id == user_id
            ).first()
            user_found = user is not None

        logger.info(f"on_new_chat_member: chat_id={chat_id} user_id={user_id} found_in_db={user_found}")

        if user_found:
            await update.message.reply_text(known_message)
            continue

        if message_text == constants.skip_on_new_chat_member_message:
            continue

        timeout_str = f"{timeout} мин." if timeout > 0 else "не установлен"
        msg_markdown = await mention_markdown(context.bot, chat_id, user_id, message_text)
        msg_markdown = msg_markdown.replace("%TIMEOUT%", timeout_str)
        msg = await update.message.reply_text(msg_markdown, parse_mode=ParseMode.MARKDOWN)

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
    """Сохраняет whois и отменяет кик. Возвращает True если кик был активен."""
    with session_scope() as sess:
        chat = sess.query(Chat).filter(Chat.id == chat_id).first()
        if chat is None:
            chat = Chat(id=chat_id)
            sess.add(chat)
            sess.flush()
        introduce_message = chat.on_introduce_message
        sess.merge(User(chat_id=chat_id, user_id=user_id, whois=message.text))

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
    if "#whois" not in message.parse_entities(types=["hashtag"]).values():
        return
    chat_id = message.chat_id
    with session_scope() as sess:
        chat = sess.query(Chat).filter(Chat.id == chat_id).first()
        min_len = chat.min_whois_length if chat else 20
    if len(message.text or "") >= min_len:
        await _process_whois(context.bot, context.job_queue, message, chat_id, message.from_user.id)


async def on_start_command(update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if update.effective_chat.id < 0:
        return

    with session_scope() as sess:
        user_chat_ids = [u.chat_id for u in sess.query(User).filter(User.user_id == user_id)]

    keyboard = []
    for chat_id in user_chat_ids:
        try:
            if await authorize_user(context.bot, chat_id, user_id):
                chat = await context.bot.get_chat(chat_id)
                title = chat.title or str(chat_id)
                keyboard.append([InlineKeyboardButton(
                    title,
                    callback_data=json.dumps({"chat_id": chat_id, "action": Actions.select_chat}),
                )])
        except Exception:
            pass

    if not keyboard:
        await update.message.reply_text("У вас нет доступных чатов.")
        return

    await update.message.reply_text(
        constants.on_start_command,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


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
        with session_scope() as sess:
            user_chat_ids = [u.chat_id for u in sess.query(User).filter(User.user_id == user_id)]

        keyboard = []
        for chat_id in user_chat_ids:
            try:
                if await authorize_user(context.bot, chat_id, user_id):
                    chat = await context.bot.get_chat(chat_id)
                    title = chat.title or str(chat_id)
                    keyboard.append([InlineKeyboardButton(
                        title,
                        callback_data=json.dumps({"chat_id": chat_id, "action": Actions.select_chat}),
                    )])
            except Exception:
                pass

        if not keyboard:
            await query.edit_message_text("У вас нет доступных чатов.")
            return

        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))

    elif data["action"] == Actions.select_chat:
        selected_chat_id = data["chat_id"]
        keyboard = [
            [InlineKeyboardButton("Изменить таймаут кика", callback_data=json.dumps(
                {"chat_id": selected_chat_id, "action": Actions.set_kick_timeout}))],
            [InlineKeyboardButton("Изменить время напоминания (мин. до кика)", callback_data=json.dumps(
                {"chat_id": selected_chat_id, "action": Actions.set_notify_delta}))],
            [InlineKeyboardButton("Изменить сообщение при входе в чат", callback_data=json.dumps(
                {"chat_id": selected_chat_id, "action": Actions.set_on_new_chat_member_message_response}))],
            [InlineKeyboardButton("Изменить сообщение при перезаходе в чат", callback_data=json.dumps(
                {"chat_id": selected_chat_id, "action": Actions.set_on_known_new_chat_member_message_response}))],
            [InlineKeyboardButton("Изменить сообщение после успешного представления", callback_data=json.dumps(
                {"chat_id": selected_chat_id, "action": Actions.set_on_successful_introducion_response}))],
            [InlineKeyboardButton("Изменить сообщение напоминания", callback_data=json.dumps(
                {"chat_id": selected_chat_id, "action": Actions.set_notify_message}))],
            [InlineKeyboardButton("Изменить сообщение после кика", callback_data=json.dumps(
                {"chat_id": selected_chat_id, "action": Actions.set_on_kick_message}))],
            [InlineKeyboardButton("Изменить сообщение при выходе из чата", callback_data=json.dumps(
                {"chat_id": selected_chat_id, "action": Actions.set_on_left_chat_member_message}))],
            [InlineKeyboardButton("Изменить напоминание написать #whois", callback_data=json.dumps(
                {"chat_id": selected_chat_id, "action": Actions.set_on_whois_reminder_message}))],
            [InlineKeyboardButton("Изменить сообщение при бане (regex)", callback_data=json.dumps(
                {"chat_id": selected_chat_id, "action": Actions.set_on_filtered_message}))],
            [InlineKeyboardButton("Изменить мин. длину #whois", callback_data=json.dumps(
                {"chat_id": selected_chat_id, "action": Actions.set_min_whois_length}))],
            [InlineKeyboardButton("Изменить длительность бана (мин.)", callback_data=json.dumps(
                {"chat_id": selected_chat_id, "action": Actions.set_ban_duration}))],
            [InlineKeyboardButton("Изменить regex для фильтра сообщений", callback_data=json.dumps(
                {"chat_id": selected_chat_id, "action": Actions.set_regex_filter}))],
            [InlineKeyboardButton("Изменить фильтрацию только для новых пользователей", callback_data=json.dumps(
                {"chat_id": selected_chat_id, "action": Actions.set_filter_only_new_users}))],
            [InlineKeyboardButton("Получить текущие настройки", callback_data=json.dumps(
                {"chat_id": selected_chat_id, "action": Actions.get_current_settings}))],
        ]
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))

    elif data["action"] in [
        Actions.set_on_new_chat_member_message_response,
        Actions.set_kick_timeout,
        Actions.set_notify_message,
        Actions.set_notify_delta,
        Actions.set_on_known_new_chat_member_message_response,
        Actions.set_on_successful_introducion_response,
        Actions.set_on_kick_message,
        Actions.set_on_left_chat_member_message,
        Actions.set_on_whois_reminder_message,
        Actions.set_on_filtered_message,
        Actions.set_min_whois_length,
        Actions.set_ban_duration,
        Actions.set_regex_filter,
        Actions.set_filter_only_new_users,
    ]:
        await query.edit_message_text(text="Отправьте новое значение")
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
            await query.edit_message_text(
                text=constants.get_settings_message.format(**{
                    k: v for k, v in chat.__dict__.items() if not k.startswith("_")
                }),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
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

        elif action in [
            Actions.set_on_new_chat_member_message_response,
            Actions.set_notify_message,
            Actions.set_on_known_new_chat_member_message_response,
            Actions.set_on_successful_introducion_response,
            Actions.set_on_kick_message,
            Actions.set_on_left_chat_member_message,
            Actions.set_on_whois_reminder_message,
            Actions.set_on_filtered_message,
            Actions.set_regex_filter,
            Actions.set_filter_only_new_users,
        ]:
            value = message.text_markdown
            with session_scope() as sess:
                if action == Actions.set_on_new_chat_member_message_response:
                    chat = Chat(id=chat_id, on_new_chat_member_message=value)
                elif action == Actions.set_on_known_new_chat_member_message_response:
                    chat = Chat(id=chat_id, on_known_new_chat_member_message=value)
                elif action == Actions.set_on_successful_introducion_response:
                    chat = Chat(id=chat_id, on_introduce_message=value)
                elif action == Actions.set_notify_message:
                    chat = Chat(id=chat_id, notify_message=value)
                elif action == Actions.set_on_kick_message:
                    chat = Chat(id=chat_id, on_kick_message=value)
                elif action == Actions.set_on_left_chat_member_message:
                    chat = Chat(id=chat_id, on_left_chat_member_message=value)
                elif action == Actions.set_on_whois_reminder_message:
                    chat = Chat(id=chat_id, on_whois_reminder_message=value)
                elif action == Actions.set_on_filtered_message:
                    chat = Chat(id=chat_id, on_filtered_message=value)
                elif action == Actions.set_filter_only_new_users:
                    chat = Chat(id=chat_id, filter_only_new_users=value.lower() in ["true", "1"])
                elif action == Actions.set_regex_filter:
                    if value == "%TURN_OFF%":
                        chat = Chat(id=chat_id, regex_filter=None)
                    else:
                        chat = Chat(id=chat_id, regex_filter=message.text)
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

    # Определяем user_id из аргумента или reply
    user_id = None
    if context.args:
        arg = context.args[0]
        try:
            # Числовой ID
            user_id = int(arg)
        except ValueError:
            # @username — резолвим через Telegram API
            try:
                member = await context.bot.get_chat_member(chat_id, arg)
                user_id = member.user.id
            except Exception:
                await message.reply_text(f"Пользователь {arg} не найден в чате.")
                return
    elif message.reply_to_message and message.reply_to_message.from_user:
        user_id = message.reply_to_message.from_user.id
    else:
        await message.reply_text("Usage: /whois @username | /whois <user_id> | ответ на сообщение")
        return

    with session_scope() as sess:
        user = sess.query(User).filter(
            User.chat_id == chat_id, User.user_id == user_id
        ).first()

        if user is None:
            await message.reply_text("Пользователь не найден в базе.")
            return

        await message.reply_text(f"whois: {user.whois}")


async def on_left_chat_member(update, context: ContextTypes.DEFAULT_TYPE):
    member = update.message.left_chat_member
    if member.is_bot:
        return
    chat_id = update.effective_chat.id
    with session_scope() as sess:
        chat = sess.query(Chat).filter(Chat.id == chat_id).first()
        if chat is None:
            return
        template = chat.on_left_chat_member_message
    if template.lower() in ["false", "0"]:
        return
    # Используем объект участника напрямую — get_chat_member ненадёжен после выхода
    user_mention = member.mention_markdown() if member.name else str(member.id)
    msg = template.replace("%USER\\_MENTION%", user_mention)
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
