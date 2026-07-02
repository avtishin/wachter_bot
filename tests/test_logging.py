"""Regression: the bot token must not leak into logs via httpx/httpcore.

httpx logs every HTTP request at INFO including the Telegram API URL, which
embeds the bot token. Importing `actions` must raise those loggers to WARNING.
"""
import logging

import actions  # noqa: F401 — import applies the logging configuration


def test_httpx_and_httpcore_silenced():
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING
