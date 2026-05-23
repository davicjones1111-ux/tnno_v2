FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd --create-home --shell /bin/bash appuser \
    && mkdir -p /app/instance /app/app/static/uploads /var/log/retroquest \
    && chown -R appuser:appuser /app /var/log/retroquest

USER appuser

EXPOSE 5000

CMD ["gunicorn", "--config", "gunicorn.conf.py", "run:app"]
