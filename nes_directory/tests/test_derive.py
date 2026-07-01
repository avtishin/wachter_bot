import alumni_derive as d


def test_split_name():
    assert d.split_name("Vylegzhanin Aleksandr Sergeevich") == ("Aleksandr", "Vylegzhanin")
    assert d.split_name("Madonna") == (None, "Madonna")
    assert d.split_name("") == (None, None)


def test_telegram_username_normalizes():
    links = [{"title": "LinkedIn", "url": "https://www.linkedin.com/in/x"},
             {"title": "Telegram", "url": "https://t.me/Very_Big_T"}]
    assert d.telegram_username(links) == "very_big_t"
    assert d.telegram_username([]) is None
    assert d.telegram_username([{"url": "https://t.me/user?start=1"}]) == "user"


def test_year_and_program_of_class():
    assert d.year_of_class("MAE'2019") == 2019
    assert d.program_code_of_class("MAE'2019") == "MAE"
    assert d.year_of_class("no year") is None


def test_emails():
    contact = {"emails": ["A@nes.ru", "b@x.com", "a@nes.ru"]}
    assert d.emails(contact) == ["a@nes.ru", "b@x.com"]
    assert d.emails({}) == []
    assert d.emails({"emails": []}) == []


def test_grad_year_max():
    assert d.grad_year_max(["BAE'2017", "MAE'2019"]) == 2019
    assert d.grad_year_max([]) is None


def test_program_title_map():
    m = d.program_title_map(["Master of Arts in Economics [MAE]", "PhD [PhD]"])
    assert m == {"MAE": "Master of Arts in Economics [MAE]", "PhD": "PhD [PhD]"}


def test_build_program_years():
    people = [
        {"listed_classes": ["MAE'2019"], "listed_programs": ["Master of Arts in Economics [MAE]"]},
        {"listed_classes": ["MAE'2012", "BAE'2015"],
         "listed_programs": ["Master of Arts in Economics [MAE]", "Bachelor of Arts in Economics [BAE]"]},
    ]
    titles, pairs = d.build_program_years(people)
    assert titles["BAE"] == "Bachelor of Arts in Economics [BAE]"
    assert ("MAE", 2019) in pairs and ("MAE", 2012) in pairs and ("BAE", 2015) in pairs
