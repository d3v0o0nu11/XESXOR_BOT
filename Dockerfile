FROM python:3.12-slim

WORKDIR /app

# System deps kept minimal; sqlite ships with the stdlib.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY xesxor ./xesxor

# Non-root user for safety.
RUN useradd --create-home --uid 10001 xesxor \
    && mkdir -p /app/data \
    && chown -R xesxor:xesxor /app
USER xesxor

ENV PYTHONUNBUFFERED=1 \
    DATABASE_PATH=/app/data/bot_database.db

EXPOSE 10000

CMD ["python", "main.py"]
