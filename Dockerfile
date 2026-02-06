FROM python:3.12-slim

RUN groupadd --gid 1000 botuser && \
    useradd --uid 1000 --gid 1000 --create-home botuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/cache && chown botuser:botuser /app/cache

USER botuser

RUN python -c "from fast_alpr import ALPR; ALPR(detector_model='yolo-v9-t-384-license-plate-end2end', ocr_model='cct-xs-v1-global-model')"

CMD ["python", "bot.py"]
