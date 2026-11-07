FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY migrations ./migrations
COPY tests ./tests
COPY pytest.ini ./pytest.ini

ENV PORT=8000
EXPOSE 8000

# Render injects PORT. Migrations run in the app lifespan before /health answers (M3 8.5).
# --proxy-headers lets the access-code rate limit see the real client IP behind Render's proxy.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port \"$PORT\" --proxy-headers --forwarded-allow-ips='*'"]
