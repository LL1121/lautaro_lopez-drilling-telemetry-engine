# syntax=docker/dockerfile:1

FROM python:3.12-slim AS builder

WORKDIR /build
COPY requirements-prod.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements-prod.txt

FROM python:3.12-slim AS runtime

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app \
    && mkdir -p /app/data \
    && chown -R app:app /app

COPY --from=builder /install /usr/local
COPY src /app/src
COPY docs/contratos /app/docs/contratos

WORKDIR /app
USER app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/app/data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health')" || exit 1

CMD ["python", "-m", "uvicorn", "src.pipeline.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
