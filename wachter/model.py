from sqlalchemy import create_engine
from sqlalchemy import Column, Integer, Text, Boolean, BigInteger, JSON, TIMESTAMP
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm.session import sessionmaker
from contextlib import contextmanager
import os

Base = declarative_base()


class Chat(Base):
    __tablename__ = 'chats'

    id = Column(BigInteger, primary_key=True)
    title = Column(Text, nullable=True)   # название чата (для дашборда)

    on_new_chat_member_message = Column(Text, nullable=False, default='Пожалуйста, представьтесь и поздоровайтесь с сообществом. У вас есть %TIMEOUT%.')
    on_known_new_chat_member_message = Column(Text, nullable=False, default='%NAME%, %CLASS% — с возвращением! 🎓')
    on_introduce_message = Column(Text, nullable=False, default='%USER\\_MENTION%, %CLASS% — добро пожаловать! 🎓\n%NAME%')
    on_alumni_welcome_message = Column(Text, nullable=False, default='Добро пожаловать в Мишпуху 2.0, %NAME% (%CLASS%)! 🤍')
    on_email_prompt_message = Column(Text, nullable=False, default='📧 Введите ваш основной e-mail, указанный в профиле на my.nes.ru (раздел «Контактная информация»). Сверим его с директорией — если найдём, сразу вас узнаем. Не помните или не совпадёт — ничего страшного, продолжим вручную.')
    on_whois_welcome_message = Column(Text, nullable=False, default='Привет, %USER\\_MENTION%.\nРады видеть тебя в Мишпухе 2.0 🤍\n\nЭто чат студентов, выпускников, сотрудников и друзей РЭШ.\nДавайте познакомимся — выберите, кто вы (у вас есть %TIMEOUT%):')
    on_whois_name_message = Column(Text, nullable=False, default='Напишите, пожалуйста, ваши Фамилию и Имя, а также пару слов о себе: где вы сейчас живёте и работаете, чем занимаетесь и в чём ваша экспертиза.')
    on_student_prompt_message = Column(Text, nullable=False, default='Напишите, пожалуйста, ваши Фамилию и Имя и пару слов о себе: что окончили или чем занимались до РЭШ, где сейчас живёте и чем увлекаетесь во внеучебное время.')
    on_friend_prompt_message = Column(Text, nullable=False, default='Напишите, пожалуйста, ваши Фамилию и Имя и пару слов о себе: что связывает вас с РЭШ и чем вы занимаетесь.')
    on_employee_prompt_message = Column(Text, nullable=False, default='Напишите, пожалуйста, ваши Фамилию и Имя и пару слов о себе: чем вы занимаетесь в РЭШ.')
    on_kick_message = Column(Text, nullable=False, default=r'%USER\_MENTION% молчит и покидает чат')
    on_left_chat_member_message = Column(Text, nullable=False, default=r'%USER\_MENTION% покинул чат')
    on_whois_reminder_message = Column(Text, nullable=False, default=r'%USER\_MENTION%, вы ещё не закончили знакомство — заполните короткую анкету кнопками выше.')
    on_filtered_message = Column(Text, nullable=False, default=r'%USER\_MENTION%, вы были забанены т.к ваше сообщение содержит репост или слово из спам листа')
    notify_message = Column(Text, nullable=False, default=r'%USER\_MENTION%, вы ещё не закончили знакомство. Пожалуйста, продолжите и заполните анкету — иначе через %MINUTES% мин. я удалю вас из чата.')
    regex_filter = Column(Text, nullable=True)
    filter_only_new_users = Column(Boolean, nullable=False, default=False)
    kick_timeout = Column(Integer, nullable=False, default=30)
    notify_delta = Column(Integer, nullable=False, default=10)
    min_whois_length = Column(Integer, nullable=False, default=20)
    ban_duration = Column(Integer, nullable=False, default=1)

    def __repr__(self):
        return f"<Chat(id={self.id})>"


class User(Base):
    __tablename__ = 'users'

    user_id = Column(BigInteger, primary_key=True)
    chat_id = Column(BigInteger, primary_key=True)

    whois = Column(Text, nullable=False)


# --- shared alumni tables (DDL owned by the scraper; bot reads/writes) -------
# Generic JSON so the bot's SQLite test DB works; in prod these are the same
# Postgres JSONB columns the scraper created.

class TgIdentity(Base):
    """Who a Telegram user is w.r.t. NES (global, cross-chat)."""
    __tablename__ = 'tg_identity'

    user_id = Column(BigInteger, primary_key=True)
    username = Column(Text)
    category = Column(Text)   # alumni|student|friend|employee|unresolved_alumni|unknown
    alumni_uid = Column(Text)
    declared_name = Column(Text)
    declared_program = Column(Text)
    declared_year = Column(Integer)
    declared_email = Column(Text)
    intro = Column(Text)
    first_seen = Column(TIMESTAMP(timezone=True))
    last_seen = Column(TIMESTAMP(timezone=True))
    verified_at = Column(TIMESTAMP(timezone=True))
    source = Column(Text)


class AlumniPerson(Base):
    """Read model of the directory snapshot (subset the bot needs)."""
    __tablename__ = 'alumni_person'

    uid = Column(Text, primary_key=True)
    name = Column(Text)
    first_name = Column(Text)
    last_name = Column(Text)
    telegram_username = Column(Text)
    emails = Column(JSON)
    programs = Column(JSON)
    classes = Column(JSON)
    grad_year_max = Column(Integer)
    full = Column(JSON)          # полная карточка (для короткого био в приветствии)
    removed_at = Column(TIMESTAMP(timezone=True))


class AlumniProgram(Base):
    """Read model of program code → title for button labels."""
    __tablename__ = 'alumni_program'

    code = Column(Text, primary_key=True)
    title = Column(Text)


class AlumniProgramYear(Base):
    """Read model of program×year pairs for the whois keyboards."""
    __tablename__ = 'alumni_program_years'

    program_code = Column(Text, primary_key=True)
    year = Column(Integer, primary_key=True)


def get_uri():
    url = os.environ.get('DATABASE_URL', 'postgresql://localhost:5432/wachter')
    # Railway выдаёт postgres://, SQLAlchemy 2.0 принимает только postgresql://
    return url.replace('postgres://', 'postgresql://', 1)


engine = create_engine(get_uri(), echo=False)
Session = sessionmaker(autoflush=True, bind=engine)


@contextmanager
def session_scope():
    session = Session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

