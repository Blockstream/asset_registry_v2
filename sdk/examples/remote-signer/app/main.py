import logging

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, field_validator

from app import config
from app.signer import sign_bytes

logger = logging.getLogger(__name__)
security = HTTPBearer(auto_error=False)


class SignRequest(BaseModel):
    message_hex: str | None = None
    message: str | None = None

    @field_validator("message_hex", "message")
    @classmethod
    def message_not_empty(cls, v: str | None) -> str | None:
        if v == "":
            raise ValueError("Message must not be empty")
        return v

    def message_bytes(self) -> bytes:
        encoded = self.message_hex or self.message
        if encoded is None:
            raise HTTPException(status_code=422, detail="message_hex is required")
        try:
            return bytes.fromhex(encoded)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="message_hex must be valid hex") from exc


app = FastAPI()


def _verify_token(credentials: HTTPAuthorizationCredentials | None = Depends(security)) -> None:
    if not credentials:
        raise HTTPException(status_code=401, detail="Missing authorization token")
    expected = config.get_signer_token()
    if credentials.credentials != expected:
        raise HTTPException(status_code=401, detail="Invalid token")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/sign")
async def sign_endpoint(
    body: SignRequest,
    _token: None = Depends(_verify_token),
):
    private_key = config.get_private_key_bytes()
    signature_hex = sign_bytes(body.message_bytes(), private_key)
    return {"signature_hex": signature_hex}


if __name__ == "__main__":
    import uvicorn

    logger.info("Starting signer server")
    uvicorn.run(app, host="0.0.0.0", port=8000)
