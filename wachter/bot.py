import os
from pathlib import Path
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    PicklePersistence,
    filters,
)
from custom_filters import filter_bot_added
import actions


def main():
    Path("data").mkdir(exist_ok=True)

    persistence = PicklePersistence(
        filepath=os.environ.get("PERSISTENCE_PATH", "data/bot_persistence")
    )

    app = (
        Application.builder()
        .token(os.environ["TELEGRAM_TOKEN"])
        .persistence(persistence)
        .build()
    )

    app.add_error_handler(actions.on_error)
    app.add_handler(CommandHandler("help", actions.on_help_command))

    app.add_handler(MessageHandler(
        filters.StatusUpdate.NEW_CHAT_MEMBERS & filter_bot_added,
        actions.on_new_chat_member,
    ))
    app.add_handler(MessageHandler(
        filters.StatusUpdate.LEFT_CHAT_MEMBER,
        actions.on_left_chat_member,
    ))
    app.add_handler(MessageHandler(
        filters.Entity("hashtag"),
        actions.on_hashtag_message,
    ))
    app.add_handler(MessageHandler(filters.FORWARDED, actions.on_forward))

    app.add_handler(CommandHandler("start", actions.on_start_command))
    app.add_handler(CommandHandler("skip", actions.on_skip_command))
    app.add_handler(CommandHandler("approve", actions.on_approve_command))
    app.add_handler(CommandHandler("whois", actions.on_whois_command))
    app.add_handler(CallbackQueryHandler(actions.on_button_click))
    app.add_handler(MessageHandler(
        filters.TEXT | filters.CAPTION,
        actions.on_message,
    ))

    app.run_polling()


if __name__ == "__main__":
    main()
