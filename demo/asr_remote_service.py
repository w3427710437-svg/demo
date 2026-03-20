import base64
import json
import os
import urllib.error
import urllib.request


def _build_remote_payload(wav_path: str) -> dict:
    with open(wav_path, "rb") as f:
        audio_b64 = base64.b64encode(f.read()).decode("utf-8")
    return {
        "audio_base64": audio_b64,
        "filename": os.path.basename(wav_path),
        "content_type": "audio/wav",
    }


def transcribe_audio_via_remote_backend(wav_path: str) -> dict:
    """
    调用外部 ASR 后端服务。
    约定接口：
      POST {ASR_BACKEND_URL}
      Body(JSON): {"audio_base64":"...","filename":"...","content_type":"audio/wav"}
      Header:
        - Content-Type: application/json
        - Authorization: Bearer <ASR_BACKEND_TOKEN>  (可选)
      返回(JSON): {"text":"...","language":"zh"} 或 {"text":"..."}
    """
    endpoint = os.getenv("ASR_BACKEND_URL", "").strip()
    if not endpoint:
        raise ValueError("未配置 ASR_BACKEND_URL。")

    timeout_sec = int(os.getenv("ASR_BACKEND_TIMEOUT_SEC", "300"))
    token = os.getenv("ASR_BACKEND_TOKEN", "").strip()

    payload = _build_remote_payload(wav_path)
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url=endpoint,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    if token:
        req.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read().decode("utf-8")
            result = json.loads(raw)
    except urllib.error.HTTPError as e:
        detail = (e.read() or b"").decode("utf-8", errors="replace")
        raise RuntimeError(f"远端 ASR HTTP 错误: {e.code}, detail={detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"远端 ASR 网络错误: {e}") from e
    except json.JSONDecodeError as e:
        raise RuntimeError("远端 ASR 返回非 JSON。") from e

    text = (result.get("text") or "").strip()
    if not text:
        raise RuntimeError("远端 ASR 返回缺少 text。")

    return {
        "text": text,
        "language": result.get("language", "zh"),
        "segments": result.get("segments", []),
    }

