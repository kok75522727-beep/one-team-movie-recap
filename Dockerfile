FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Extract the Owner source bundle during the Railway build.
COPY one-team-movie-recap-source-owner-key.zip /tmp/one-team-movie-recap-source-owner-key.zip
RUN python -c "import zipfile; zipfile.ZipFile('/tmp/one-team-movie-recap-source-owner-key.zip').extractall('/tmp/source')" \
    && cp -a /tmp/source/nicegui_one_team/. /app/ \
    && rm -rf /tmp/source /tmp/one-team-movie-recap-source-owner-key.zip

RUN python -m pip install --no-cache-dir -r requirements.txt

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

EXPOSE 8080
CMD ["python", "main.py"]
