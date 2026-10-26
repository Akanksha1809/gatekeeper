# syntax=docker/dockerfile:1
# --- builder: resolve and install dependencies with uv ---
FROM python:3.12-slim AS builder

RUN pip install --no-cache-dir --default-timeout=120 --retries=5 uv==0.5.11

WORKDIR /build
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project -o requirements.txt

FROM python:3.12-slim AS runtime

RUN groupadd --gid 1000 gatekeeper && \
    useradd --uid 1000 --gid gatekeeper --shell /usr/sbin/nologin --create-home gatekeeper

WORKDIR /app

COPY --from=builder /build/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

COPY gatekeeper ./gatekeeper
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini
COPY config ./config

RUN chown -R gatekeeper:gatekeeper /app
USER gatekeeper

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz', timeout=2)" || exit 1

CMD ["uvicorn", "gatekeeper.main:app", "--host", "0.0.0.0", "--port", "8000"]
