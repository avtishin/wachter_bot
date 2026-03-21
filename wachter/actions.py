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
        target_user_id = message.reply_to_message.from_user.id
        issuer_user_id = message.from_user.id

        if not await authorize_user(context.bot, chat_id, issuer_user_id):
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

    for member in update.message.new_chat_members:
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
            if timeout >= 10:
                context.job_queue.run_once(
                    on_notify_timeout,
                    (timeout - constants.notify_delta) * 60,
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
        msg_markdown = await mention_markdown(
            context.bot, data["chat_id"], data["user_id"], chat.notify_message
        )
    message = await context.bot.send_message(
        data["chat_id"], text=msg_markdown, parse_mode=ParseMode.MARKDOWN
    )
    context.job_queue.run_once(
        delete_message,
        constants.notify_delta * 60,
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
        await context.bot.ban_chat_member(
            data["chat_id"],
            data["user_id"],
            until_date=datetime.now() + timedelta(seconds=60),
        )
        with session_scope() as sess:
            chat = sess.query(Chat).filter(Chat.id == data["chat_id"]).first()
            if chat.on_kick_message.lower() not in ["false", "0"]:
                msg_markdown = await mention_markdown(
                    context.bot, data["chat_id"], data["user_id"], chat.on_kick_message
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
        return

    if message.reply_to_message is None:
        await message.reply_text("Ответьте на сообщение пользователя, которого нужно одобрить.")
        return

    reply_from = message.reply_to_message.from_user
    if reply_from is None or reply_from.is_bot:
        await message.reply_text("Нельзя одобрить бота. Ответьте на сообщение пользователя.")
        return

    target_user_id = reply_from.id

    with session_scope() as sess:
        sess.merge(User(chat_id=chat_id, user_id=target_user_id, whois="Одобрен администратором"))

    await cancel_kick_jobs(context.bot, context.job_queue, chat_id, target_user_id)
    await message.reply_text("Пользователь одобрен.")


async def on_hashtag_message(update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat_id = message.chat_id

    if (
        "#whois" in message.parse_entities(types=["hashtag"]).values()
        and len(message.text or "") >= constants.min_whois_length
        and chat_id < 0
    ):
        user_id = message.from_user.id

        with session_scope() as sess:
            chat = sess.query(Chat).filter(Chat.id == chat_id).first()
            if chat is None:
                chat = Chat(id=chat_id)
                sess.add(chat)
                sess.flush()
            introduce_message = chat.on_introduce_message
            sess.merge(User(chat_id=chat_id, user_id=user_id, whois=message.text))

        removed = await cancel_kick_jobs(context.bot, context.job_queue, chat_id, user_id)
        if removed:
            msg_markdown = await mention_markdown(context.bot, chat_id, user_id, introduce_message)
            await message.reply_text(msg_markdown, parse_mode=ParseMode.MARKDOWN)
    else:
        await on_message(update, context)


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
    data = json.loads(query.data)

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
        Actions.set_on_known_new_chat_member_message_response,
        Actions.set_on_successful_introducion_response,
        Actions.set_on_kick_message,
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
            await query.edit_message_text(
                text=constants.get_settings_message.format(**chat.__dict__),
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
        return re.search(chat.regex_filter, message_text)


async def on_forward(update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat_id = message.chat_id
    user_id = message.from_user.id

    if chat_id > 0 or await authorize_user(context.bot, chat_id, user_id):
        return

    with session_scope() as sess:
        chat = sess.query(Chat).filter(Chat.id == chat_id).first()
        if chat is None or chat.regex_filter is None:
            return

    removed = await cancel_kick_jobs(context.bot, context.job_queue, chat_id, user_id)
    if removed:
        await context.bot.delete_message(chat_id, message.message_id)
        msg_markdown = await mention_markdown(context.bot, chat_id, user_id, constants.on_filtered_message)
        await context.bot.send_message(chat_id, text=msg_markdown, parse_mode=ParseMode.MARKDOWN)
        await context.bot.ban_chat_member(
            chat_id, user_id, until_date=datetime.now() + timedelta(seconds=60)
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
        user_id = message.from_user.id
        message_text = message.text or message.caption
        filter_mask = (
            not await authorize_user(context.bot, chat_id, user_id)
            and filter_message(chat_id, message_text)
        )
        if is_chat_filters_new_users(chat_id):
            filter_mask = filter_mask and is_new_user(chat_id, user_id)

        if filter_mask:
            await context.bot.delete_message(chat_id, message.message_id)
            msg_markdown = await mention_markdown(
                context.bot, chat_id, user_id, constants.on_filtered_message
            )
            await cancel_kick_jobs(context.bot, context.job_queue, chat_id, user_id)
            await context.bot.send_message(chat_id, text=msg_markdown, parse_mode=ParseMode.MARKDOWN)
            await context.bot.ban_chat_member(
                chat_id, user_id, until_date=datetime.now() + timedelta(seconds=60)
            )
    else:
        user_id = chat_id
        action = context.user_data.get("action")

        if action is None:
            return

        chat_id = context.user_data["chat_id"]

        if not await authorize_user(context.bot, chat_id, user_id):
            await message.reply_text("У вас нет прав для изменения настроек этого чата.")
            context.user_data["action"] = None
            return

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

            keyboard = [[
                InlineKeyboardButton("К настройке чата", callback_data=json.dumps(
                    {"chat_id": chat_id, "action": Actions.select_chat})),
                InlineKeyboardButton("К списку чатов", callback_data=json.dumps(
                    {"action": Actions.start_select_chat})),
            ]]
            await message.reply_text(
                constants.on_success_set_kick_timeout_response,
                reply_markup=InlineKeyboardMarkup(keyboard),
            )

        elif action in [
            Actions.set_on_new_chat_member_message_response,
            Actions.set_notify_message,
            Actions.set_on_known_new_chat_member_message_response,
            Actions.set_on_successful_introducion_response,
            Actions.set_on_kick_message,
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
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /whois <user_id>")
        return

    chat_id = update.effective_chat.id
    user_id = context.args[0]

    with session_scope() as sess:
        user = sess.query(User).filter(
            User.chat_id == chat_id, User.user_id == user_id
        ).first()

        if user is None:
            await update.message.reply_text("user not found")
            return

        await update.message.reply_text(f"whois: {user.whois}")
