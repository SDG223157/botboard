# syntax=docker/dockerfile:1
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl libpq-dev netcat-openbsd && \
    rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./

EXPOSE 8080

# Explicitly set app dir for uvicorn module discovery
CMD ["bash", "-lc", "uvicorn main:app --app-dir app --host 0.0.0.0 --port 8080"]
