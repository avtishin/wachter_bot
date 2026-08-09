FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

# code only; data (out/, raw_html/) comes from a mounted volume
COPY *.py ./
COPY templates ./templates

EXPOSE 8000

# single worker on purpose: the scheduler thread + file lock assume one process.
# shell-form CMD so ${PORT} (injected by Railway) expands; fallback 8000 locally.
CMD gunicorn -w 1 --threads 8 -b "0.0.0.0:${PORT:-8000}" --timeout 120 app:app
