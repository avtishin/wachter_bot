FROM python:3.12-slim

RUN pip install pipenv

COPY Pipfile /Pipfile
COPY Pipfile.lock /Pipfile.lock

RUN pipenv install --system

COPY . /app
WORKDIR /app

RUN mkdir -p data

CMD alembic upgrade head && python wachter/bot.py
