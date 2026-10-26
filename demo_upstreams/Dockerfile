# syntax=docker/dockerfile:1
# Shared image for all three demo upstream services. Which service runs is
# chosen entirely by docker-compose's `command` for that container -- this
# avoids maintaining three near-identical Dockerfiles.
FROM python:3.12-slim AS runtime

RUN groupadd --gid 1000 demo && \
    useradd --uid 1000 --gid demo --shell /usr/sbin/nologin --create-home demo

RUN pip install --no-cache-dir fastapi==0.115.6 "uvicorn[standard]==0.34.0"

WORKDIR /app
COPY demo_upstreams ./demo_upstreams

RUN chown -R demo:demo /app
USER demo

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://localhost:{os.environ.get(\"PORT\",\"9001\")}/health', timeout=2)" || exit 1

CMD ["sh", "-c", "uvicorn demo_upstreams.${SERVICE_MODULE}.main:app --host 0.0.0.0 --port ${PORT}"]
