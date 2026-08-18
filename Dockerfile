FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --require-hashes -r requirements.txt

COPY pyproject.toml README.md LICENSE ./
COPY registry_api ./registry_api
RUN pip install --no-cache-dir --no-deps .

COPY alembic.ini ./
COPY migrations ./migrations
RUN adduser --disabled-password --gecos "" --no-create-home appuser
USER appuser

EXPOSE 8000
# Uvicorn populates request.client from forwarded headers only when the socket
# peer is trusted by FORWARDED_ALLOW_IPS (127.0.0.1 by default). The
# application rate limiter intentionally relies on that single resolution step.
CMD ["uvicorn", "registry_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
