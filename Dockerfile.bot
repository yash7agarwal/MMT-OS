# MMT-OS Telegram Bot — lightweight cloud image (no Android SDK)
#
# The bot is the always-on interface. UAT execution happens on a
# machine with a connected device (Mac or dedicated device host).
#
# Build:  docker build -f Dockerfile.bot -t mmt-os-bot .
# Run:    docker run --env-file .env mmt-os-bot

FROM python:3.11-slim

WORKDIR /app

# Only install bot + API deps (no uiautomator2, no Android tooling)
COPY requirements.bot.txt .
RUN pip install --no-cache-dir -r requirements.bot.txt

COPY . .

RUN mkdir -p apks reports memory .tmp/evidence

ENV PYTHONPATH=/app

CMD ["python", "-m", "telegram_bot.run_bot"]
