"""Регрессии по markdown-инъекции через подконтрольный пользователю текст."""
from types import SimpleNamespace

import actions


def test_escape_md_v1_removes_unescapable_bracket():
    # ] в v1 не экранируется — удаляем; [ экранируется
    assert "]" not in actions.escape_md_v1("a]b]c")
    assert actions.escape_md_v1("[x]") == "\\[x"
    assert actions.escape_md_v1(None) == ""


def test_safe_mention_neutralizes_link_injection():
    # имя-атака: попытка подмешать фишинг-ссылку в сообщение бота
    user = SimpleNamespace(full_name="](tg://user?id=1) [evil](http://phish)",
                           name="x", id=1)
    m = actions.safe_mention(user)
    # ровно одна ссылка — само упоминание; чужая ссылка не собирается
    assert m.count("](tg://user?id=1)") == 1
    assert "](http://phish)" not in m
    assert m.startswith("[") and m.endswith("](tg://user?id=1)")


def test_safe_mention_plain_name():
    user = SimpleNamespace(full_name="Иван Петров", name="ivan", id=42)
    assert actions.safe_mention(user) == "[Иван Петров](tg://user?id=42)"


def test_split_message_respects_limit_and_keeps_content():
    assert actions.split_message("коротко") == ["коротко"]
    lines = [f"строка {i}" for i in range(500)]
    chunks = actions.split_message("\n".join(lines), limit=100)
    assert len(chunks) > 1
    assert all(len(c) <= 100 for c in chunks)
    assert "\n".join(chunks).split("\n") == lines   # ничего не потеряно, порядок сохранён
