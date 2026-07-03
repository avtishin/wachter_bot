"""Cyrillic → Latin transliteration matching the scheme used by the NES
directory (Александр→Aleksandr, Вылегжанин→Vylegzhanin, Хоруженко→Khoruzhenko).

Used to let the dashboard search Latin-stored names by a Russian query.
"""

_RU2LAT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def ru_to_lat(s):
    """Transliterate a (possibly mixed) string; non-Cyrillic chars pass through."""
    return "".join(_RU2LAT.get(ch, ch) for ch in (s or "").lower())


def has_cyrillic(s):
    return any("а" <= ch <= "я" or ch in "ёЁ" or "А" <= ch <= "Я" for ch in (s or ""))
