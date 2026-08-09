import translit as t


def test_ru_to_lat_matches_nes_scheme():
    assert t.ru_to_lat("Тишин") == "tishin"
    assert t.ru_to_lat("Александр") == "aleksandr"
    assert t.ru_to_lat("Вылегжанин") == "vylegzhanin"
    assert t.ru_to_lat("Хоруженко") == "khoruzhenko"
    assert t.ru_to_lat("Кожевникова") == "kozhevnikova"


def test_ru_to_lat_passthrough_and_cyrillic_flag():
    assert t.ru_to_lat("Tishin") == "tishin"   # latin passes through (lowercased)
    assert t.has_cyrillic("Николай") is True
    assert t.has_cyrillic("Nikolay") is False
