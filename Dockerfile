# Prism — single image, both services (prism-api + prism-bot).
#
# The Railway start command differs per service (RAILWAY_RUN_COMMAND env var):
#   prism-api: uvicorn webapp.api.main:app --host 0.0.0.0 --port $PORT
#   prism-bot: python -m telegram_bot.run_bot
#
# Build locally:  docker build -t prism .
# Run API:        docker run -p 8000:8000 --env-file .env prism \
#                   uvicorn webapp.api.main:app --host 0.0.0.0 --port 8000
# Run bot:        docker run --env-file .env prism

FROM python:3.11-slim

WORKDIR /app

# Full deps — covers both API (fastapi/uvicorn/sqlalchemy) and bot
# (python-telegram-bot/httpx), plus shared anthropic/gemini clients.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Writable dirs used at runtime — Railway volume overlays /app/webapp/data.
RUN mkdir -p memory .tmp/evidence webapp/data/screenshots

ENV PYTHONPATH=/app

# Default to the bot; Railway overrides per-service via RAILWAY_RUN_COMMAND.
CMD ["python", "-m", "telegram_bot.run_bot"]
