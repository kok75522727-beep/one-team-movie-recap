# This file deploys the uploaded One Team source ZIP directly on Railway.
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg unzip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY one-team-movie-recap-source.zip /tmp/one-team-source.zip
RUN unzip -q /tmp/one-team-source.zip -d /build \
    && mv /build/nicegui_one_team /app \
    && rm /tmp/one-team-source.zip

WORKDIR /app
RUN python -m pip install --no-cache-dir -r requirements.txt

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

EXPOSE 8080
CMD ["python", "main.py"]
