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

# WeasyPrint (v0.17.0 report generator) needs Pango for HTML→PDF.
# libpango-1.0-0 + libpangoft2-1.0-0 = ~5MB on Debian slim; everything
# else (fonts, harfbuzz) comes in as transitive apt deps.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 \
        fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Full deps — covers both API (fastapi/uvicorn/sqlalchemy) and bot
# (python-telegram-bot/httpx), plus shared anthropic/gemini clients.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Writable dirs used at runtime — Railway volume overlays /app/webapp/data.
RUN mkdir -p memory .tmp/evidence webapp/data/screenshots

ENV PYTHONPATH=/app

# Service dispatch via env var — each Railway service sets SERVICE_TYPE:
#   prism-api → SERVICE_TYPE=api  (uvicorn)
#   prism-bot → SERVICE_TYPE=bot  (telegram polling)
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh
ENTRYPOINT ["/docker-entrypoint.sh"]
