FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY migrations ./migrations
COPY tests ./tests
COPY benchmarks ./benchmarks
COPY pytest.ini ./pytest.ini

ENV PORT=8000
EXPOSE 8000

# Render injects PORT. Migrations run in the app lifespan before /health answers (M3 8.5).
# Proxy headers are NOT trusted wholesale: --forwarded-allow-ips='*' made the left-most,
# client-controlled X-Forwarded-For entry the client address and let anyone bypass the
# access-code rate limit. The limiter reads the entry Render appends (TRUSTED_PROXY_HOPS=1).
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port \"$PORT\" --no-proxy-headers --no-access-log"]
