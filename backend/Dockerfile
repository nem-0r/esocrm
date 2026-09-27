FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
# ffmpeg: голосовые в формат Telegram (Ogg/Opus), размеры и длительность видео,
# кадр-превью, AAC-копия голосового для Safari (docs/14-release-plan-2026-09-27.md, §2).
RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install -e ".[dev]"

FROM base AS dev
COPY . .

FROM base AS prod
COPY . .
RUN pip install -e .
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
