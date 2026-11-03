FROM python:3.12-alpine AS builder

ARG WALLYCORE_VERSION=1.5.6
ARG WALLYCORE_SDIST_SHA256=30190ab70803484f569f3341337730134d291dd01a4e4b07028b94c7a42771d9
ARG WALLYCORE_SDIST_URL=https://files.pythonhosted.org/packages/dd/f1/cc75bc4af58d0769693171a5d085ae01a047f839a1e2b802399890f0159a/wallycore-1.5.6.tar.gz
ARG WALLYCORE_SECP_MACRO_SHA256=1f533c7f25871e1abde54f3d2f625eb8c6e5812c00aad19c5ef99752bf7688b1

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH

RUN apk add --no-cache \
    autoconf \
    automake \
    bash \
    build-base \
    libtool \
    swig

RUN python -m venv /opt/venv

WORKDIR /build

COPY requirements.txt ./
RUN mkdir /tmp/wally-source && \
    wget \
        -O "/tmp/wally-source/wallycore-$WALLYCORE_VERSION.tar.gz" \
        "$WALLYCORE_SDIST_URL" && \
    echo "$WALLYCORE_SDIST_SHA256  /tmp/wally-source/wallycore-$WALLYCORE_VERSION.tar.gz" \
        | sha256sum -c - && \
    mkdir /tmp/wally-build && \
    tar -xzf "/tmp/wally-source/wallycore-$WALLYCORE_VERSION.tar.gz" \
        --strip-components=1 \
        -C /tmp/wally-build

COPY docker/wallycore/bitcoin_secp.m4 \
    /tmp/wally-build/src/secp256k1/autotools-aux/m4/bitcoin_secp.m4

# The upstream Python image exposes PYTHON_VERSION as the full patch version.
# Wallycore's Autoconf macro interprets that variable as an executable suffix
# (for example, python3.12.14), so clear it while building the native binding.
RUN echo "$WALLYCORE_SECP_MACRO_SHA256  /tmp/wally-build/src/secp256k1/autotools-aux/m4/bitcoin_secp.m4" \
        | sha256sum -c - && \
    unset PYTHON_VERSION && \
    python -m pip wheel \
        --no-cache-dir \
        --no-deps \
        --wheel-dir=/tmp/wally-wheel \
        /tmp/wally-build && \
    python -m pip install \
        --no-cache-dir \
        --no-index \
        --no-deps \
        --find-links=/tmp/wally-wheel \
        "wallycore==$WALLYCORE_VERSION" && \
    python -m pip install --no-cache-dir --require-hashes -r requirements.txt

COPY pyproject.toml README.md LICENSE ./
COPY registry_api ./registry_api
RUN python -m pip install --no-cache-dir --no-deps .

FROM python:3.12-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app

COPY alembic.ini ./
COPY migrations ./migrations
COPY --from=builder /opt/venv /opt/venv

RUN adduser -D -H appuser
USER appuser

EXPOSE 8000
# Uvicorn populates request.client from forwarded headers only when the socket
# peer is trusted by FORWARDED_ALLOW_IPS (127.0.0.1 by default). The
# application rate limiter intentionally relies on that single resolution step.
CMD ["uvicorn", "registry_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
