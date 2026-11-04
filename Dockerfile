FROM python:3.13-slim-trixie AS build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Build the venv against the interpreter path provided by distroless.
# Use the builder's pip so no package manager is installed in the runtime venv.
RUN ln -s /usr/local/bin/python3 /usr/bin/python3 \
    && /usr/bin/python3 -m venv --without-pip /opt/venv

COPY requirements.txt ./
RUN pip --python /opt/venv install --no-cache-dir --require-hashes -r requirements.txt

COPY pyproject.toml README.md LICENSE ./
COPY registry_api ./registry_api
RUN pip --python /opt/venv install --no-cache-dir --no-deps .

FROM gcr.io/distroless/python3-debian13:nonroot

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

COPY --from=build /opt/venv /opt/venv

COPY alembic.ini ./
COPY migrations ./migrations

# Allow console scripts (including Compose overrides) instead of inheriting
# the base image's Python entrypoint.
ENTRYPOINT []

EXPOSE 8000
# Uvicorn populates request.client from forwarded headers only when the socket
# peer is trusted by FORWARDED_ALLOW_IPS (127.0.0.1 by default). The
# application rate limiter intentionally relies on that single resolution step.
CMD ["uvicorn", "registry_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
