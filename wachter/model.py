from sqlalchemy import create_engine
from sqlalchemy import Column, Integer, String, Text, Boolean, BigInteger
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm.session import sessionmaker
from contextlib import contextmanager
import enum
import os
from sqlalchemy import inspect

Base = declarative_base()


class Chat(Base):
    __tablename__ = 'chats'

    id = Column(BigInteger, primary_key=True)

    on_new_chat_member_message = Column(Text, nullable=False, default=f'Привет %USER_MENTION%, Это Мишпуха 2.0 без ненависти. '
                                                                      'Напиши пару слов (30 символов) с тегом #whois, кто ты и чем занимаешься.'
                                                                      ' Мы хотим создавать сообщество для студентов, выпускников,'
                                                                      ' сотрудников и друзей РЭШ, поэтому, '
                                                                      'если ты не представишься в течение 30 минут, '
                                                                      'мы будем вынуждены тебя удалить из чата.')
    on_known_new_chat_member_message = Column(Text, nullable=False, default='Добро пожаловать. Снова')
    on_introduce_message = Column(Text, nullable=False, default='Добро пожаловать.')
    on_introduce_message_small_whois = Column(Text, nullable=False, default='%USER\_MENTION%, твое сообщение слишком короткое, '
                                                                            'напиши о себе подробнее.')
    on_kick_message = Column(Text, nullable=False, default='%USER\_MENTION% молчит и покидает чат')
    notify_message = Column(Text, nullable=False, default='%USER\_MENTION%, пожалуйста, представьтесь и поздоровайтесь с сообществом.')
    regex_filter = Column(Text, nullable=True)
    filter_only_new_users = Column(Boolean, nullable=False, default=False)
    kick_timeout = Column(Integer, nullable=False, default=0)

    def __repr__(self):
        return f"<Chat(id={self.id})>"


class User(Base):
    __tablename__ = 'users'

    user_id = Column(BigInteger, primary_key=True)
    chat_id = Column(BigInteger, primary_key=True)

    whois = Column(Text, nullable=False)


def get_uri():
    return os.environ.get('DATABASE_URL', 'postgresql://localhost:5432/wachter')


engine = create_engine(get_uri(), echo=False)
Session = sessionmaker(autoflush=True, bind=engine)


@contextmanager
def session_scope():
    session = Session()
    try:
        yield session
        session.commit()
    except:
        session.rollback()
        raise
    finally:
        session.close()

def orm_to_dict(obj):
    return obj._asdict()
