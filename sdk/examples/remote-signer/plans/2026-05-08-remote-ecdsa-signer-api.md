---
name: remote ECDSA signer API server
description: Python API server with token-protected endpoint that signs messages using ECDSA in Bitcoin signed message format
type: project
---

# Remote ECDSA Signer API Server

## Overview

A minimal Python API server with a single POST endpoint that signs an input string using ECDSA (secp256k1) in the Bitcoin signed message format, returning the raw signature as a hex string. The endpoint is protected by a static Bearer token.

## Resolved Requirements

| Requirement | Decision |
|---|---|
| Private key source | Hex string via `PRIVATE_KEY_HEX` env var |
| Signature format | Raw R‖S (64 or 65 bytes), hex-encoded |
| Bearer token | Environment variable `SIGNER_TOKEN` only |
| Containerization | Docker + docker-compose |
| Framework | FastAPI + uvicorn |
| Language | Python 3.11+ |

## Endpoint

```
POST /sign
Authorization: Bearer <token>
Content-Type: application/json

Body: { "message": "string to sign" }
Response: { "signature_hex": "0123abcd..." }
```

- 401 if token is missing/invalid
- 400 if `message` field is missing or empty

## Bitcoin Signed Message Format

The Bitcoin signed message format uses a specific prefix before the message bytes:

```
"<magic><len><message>"
```

Where `<magic>` is `Bitcoin Signed Message:\n` and `<len>` is the BTL-encoded length of the message.

The ECDSA signature over this prefixed data is returned as raw R‖S bytes (recovered ID prepended if applicable), hex-encoded.

## Project Structure

```
remote-signer/
  plans/
    2026-05-08-remote-ecdsa-signer-api.md   <-- this plan
  app/
    __init__.py
    main.py                                 # FastAPI app, endpoint, auth middleware
    signer.py                               # ECDSA signing logic, Bitcoin format
    config.py                               # Env var loading, validation
  requirements.txt
  Dockerfile
  docker-compose.yml
  .env.example
  .dockerignore
```

## Dependencies

```
fastapi
uvicorn
ecdsa
pycryptodome        # for secp256k1 curve parameters if needed
python-dotenv
```

## Module Work Items

### Module 1: Project Scaffolding

Set up the directory structure, dependencies, and configuration loading.

- [x] Create `app/` package with `__init__.py`
- [x] Create `requirements.txt` with pinned dependencies
- [x] Create `app/config.py` — load `SIGNER_TOKEN` and `PRIVATE_KEY_HEX` from env, validate presence and format
- [x] Create `.env.example` with placeholder values
- [x] Create `.dockerignore`

### Module 2: Signer Module

Implement Bitcoin signed message formatting and ECDSA signing.

- [x] Create `app/signer.py`
- [x] Implement BTL variable-length integer encoding
- [x] Implement Bitcoin message prefix construction (`Bitcoin Signed Message:\n` + BTL length + message)
- [x] Implement ECDSA signing over prefixed data using secp256k1 with private key from config
- [x] Return raw R‖S signature bytes (recover ID prepended as 27/28 byte for low-S / high-S)
- [x] Hex-encode the raw signature for JSON response
- [x] Unit tests for signer (various message lengths, edge cases like empty-ish strings, multi-byte chars)

### Module 3: FastAPI Application

Build the API layer with authentication and request handling.

- [x] Create `app/main.py`
- [x] Initialize FastAPI app with startup logging (key loaded, token set)
- [x] Implement Bearer token dependency (`Depends`) returning 401 on invalid/missing token
- [x] Implement `POST /sign` endpoint accepting `{message: str}`, returning `{signature_hex: str}`
- [x] Input validation — return 422 if message is missing or empty (Pydantic validator)
- [x] Health check endpoint (`GET /health` → `{status: "ok"}`) — no auth required
- [x] Integration tests for endpoint (valid token, invalid token, no token, missing message)

### Module 4: Docker Containerization

Containerize the application for deployment.

- [x] Create `Dockerfile` — slim Python 3.11 image, non-root user, copy requirements → install → copy app → run uvicorn
- [x] Create `docker-compose.yml` — single service, port mapping (e.g. 8000:8000), env_file reference, restart policy
- [x] Build and verify container starts correctly
- [x] Smoke test: `curl` the `/sign` endpoint against the running container

### Module 5: End-to-End Verification

Final validation that the full pipeline works as expected.

- [x] Sign a known message, verify result matches a reference implementation (e.g. Bitcoin Core `signmessage` output format)
- [x] Verify 401 responses for missing/wrong token
- [x] Verify 400 responses for missing/empty message body
- [x] Document example curl commands in `.env.example` or README
