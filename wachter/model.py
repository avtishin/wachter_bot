from sqlalchemy import create_engine
from sqlalchemy import Column, Integer, Text, Boolean, BigInteger
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm.session import sessionmaker
from contextlib import contextmanager
import os

Base = declarative_base()


class Chat(Base):
    __tablename__ = 'chats'

    id = Column(BigInteger, primary_key=True)

    on_new_chat_member_message = Column(Text, nullable=False, default='Пожалуйста, представьтесь и поздоровайтесь с сообществом. У вас есть %TIMEOUT%.')
    on_known_new_chat_member_message = Column(Text, nullable=False, default='Добро пожаловать. Снова')
    on_introduce_message = Column(Text, nullable=False, default='Добро пожаловать.')
    on_kick_message = Column(Text, nullable=False, default=r'%USER\_MENTION% молчит и покидает чат')
    on_left_chat_member_message = Column(Text, nullable=False, default=r'%USER\_MENTION% покинул чат')
    on_whois_reminder_message = Column(Text, nullable=False, default=r'%USER\_MENTION%, напишите сообщение с тегом \#whois (минимум %MIN\_LENGTH% символов), чтобы представиться.')
    on_filtered_message = Column(Text, nullable=False, default=r'%USER\_MENTION%, вы были забанены т.к ваше сообщение содержит репост или слово из спам листа')
    notify_message = Column(Text, nullable=False, default=r'%USER\_MENTION%, пожалуйста, представьтесь и поздоровайтесь с сообществом.')
    regex_filter = Column(Text, nullable=True)
    filter_only_new_users = Column(Boolean, nullable=False, default=False)
    kick_timeout = Column(Integer, nullable=False, default=0)
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

