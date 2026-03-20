import base64
import os
import tempfile

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from asr_service import transcribe_audio


class TranscribeRequest(BaseModel):
    audio_base64: str
    filename: str = "upload.wav"
    content_type: str = "audio/wav"


app = FastAPI(title="UMU Remote ASR Backend", version="1.0.0")


def _check_auth(authorization: str | None) -> None:
    token = os.getenv("ASR_BACKEND_TOKEN", "").strip()
    if not token:
        return
    if authorization != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="unauthorized")


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.post("/transcribe")
def transcribe(req: TranscribeRequest, authorization: str | None = Header(default=None)) -> dict:
    _check_auth(authorization)

    try:
        audio_bytes = base64.b64decode(req.audio_base64)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"invalid_base64: {exc}") from exc

    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.write(audio_bytes)
    tmp.close()

    try:
        result = transcribe_audio(tmp.name)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"asr_failed: {exc}") from exc
    finally:
        try:
            os.remove(tmp.name)
        except OSError:
            pass

    return {
        "text": result.get("text", ""),
        "language": result.get("language", "zh"),
        "segments": result.get("segments", []),
    }

