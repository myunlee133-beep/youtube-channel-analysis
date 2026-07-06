FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    RUNS_DIR=/app/runs \
    MPLCONFIGDIR=/tmp/youtube-analysis-cache/matplotlib \
    XDG_CACHE_HOME=/tmp/youtube-analysis-cache

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        ffmpeg \
        fonts-noto-cjk \
        nodejs \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY analyze.py app.py collect.py koreatext.py ./
COPY web ./web

RUN mkdir -p /app/runs /tmp/youtube-analysis-cache

EXPOSE 8000

CMD ["python", "app.py"]
