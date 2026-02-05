FROM python:3.12-slim

RUN groupadd --gid 1000 botuser && \
    useradd --uid 1000 --gid 1000 --create-home botuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/cache && chown botuser:botuser /app/cache

USER botuser

CMD ["python", "bot.py"]
