FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Time zone data, so TZ (e.g. Europe/Amsterdam) sets the time used for
# schedules and alerts. The slim image has none.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Healthy while the heartbeat monitor writes data/heartbeat, which it
# does every minute while the bot is connected to Discord.
HEALTHCHECK --interval=2m --timeout=10s --start-period=5m --retries=3 \
    CMD python -c "import sys, time; sys.exit(time.time() - int(open('/app/data/heartbeat').read()) > 300)"

CMD ["python", "bot.py"]
