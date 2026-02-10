FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1000 botuser && \
    useradd --uid 1000 --gid 1000 --create-home botuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/cache && chown botuser:botuser /app/cache

USER botuser

RUN python -c "from fast_alpr import ALPR; ALPR(detector_model='yolo-v9-t-384-license-plate-end2end', ocr_model='cct-xs-v1-global-model')"
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('base', device='cpu', compute_type='int8')"

CMD ["python", "bot.py"]
