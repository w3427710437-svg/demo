import base64
import datetime
import hashlib
import hmac
import json
import os
import ssl
import tempfile
import threading
import time
import wave
import audioop
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from time import mktime
from urllib.parse import urlencode
from wsgiref.handlers import format_date_time

import websocket


@dataclass
class AsrConfig:
    app_id: str
    api_key: str
    api_secret: str
    ws_host: str = "iat.xf-yun.com"
    ws_path: str = "/v1"
    ws_scheme: str = "ws"
    chunk_duration_sec: float = 50.0
    overlap_sec: float = 0.8
    sample_rate: int = 16000
    encoding: str = "raw"
    # 多语言转写：若需要只中文可将环境变量 ASR_LANGUAGE 设为 zh_cn
    language: str = "mul_cn"
    accent: str = "mandarin"
    frame_bytes: int = 1280
    frame_interval_sec: float = 0.04
    timeout_sec: int = 120
    max_workers: int = 10
    retries_per_chunk: int = 2
    retry_backoff_sec: float = 0.5

    @classmethod
    def from_env(cls) -> "AsrConfig":
        app_id = os.getenv("ASR_APP_ID", "").strip()
        api_key = os.getenv("ASR_API_KEY", "").strip()
        api_secret = os.getenv("ASR_API_SECRET", "").strip()
        language = os.getenv("ASR_LANGUAGE", "").strip() or cls.language
        accent = os.getenv("ASR_ACCENT", "").strip() or cls.accent
        chunk_duration_sec = float(os.getenv("ASR_CHUNK_DURATION_SEC", str(cls.chunk_duration_sec)))
        overlap_sec = float(os.getenv("ASR_OVERLAP_SEC", str(cls.overlap_sec)))
        frame_interval_sec = float(os.getenv("ASR_FRAME_INTERVAL_SEC", str(cls.frame_interval_sec)))
        timeout_sec = int(os.getenv("ASR_TIMEOUT_SEC", str(cls.timeout_sec)))
        max_workers = int(os.getenv("ASR_MAX_WORKERS", str(cls.max_workers)))
        retries_per_chunk = int(os.getenv("ASR_RETRIES_PER_CHUNK", str(cls.retries_per_chunk)))
        retry_backoff_sec = float(os.getenv("ASR_RETRY_BACKOFF_SEC", str(cls.retry_backoff_sec)))
        if not app_id or not api_key or not api_secret:
            raise ValueError("缺少 ASR 配置，请在 .env 中设置 ASR_APP_ID / ASR_API_KEY / ASR_API_SECRET。")
        return cls(
            app_id=app_id,
            api_key=api_key,
            api_secret=api_secret,
            language=language,
            accent=accent,
            chunk_duration_sec=max(1.0, chunk_duration_sec),
            overlap_sec=max(0.0, overlap_sec),
            frame_interval_sec=max(0.0, frame_interval_sec),
            timeout_sec=max(10, timeout_sec),
            max_workers=max(1, min(50, max_workers)),
            retries_per_chunk=max(0, retries_per_chunk),
            retry_backoff_sec=max(0.0, retry_backoff_sec),
        )


def _auth_url(config: AsrConfig) -> str:
    base_url = f"{config.ws_scheme}://{config.ws_host}{config.ws_path}"
    now = datetime.datetime.now()
    date = format_date_time(mktime(now.timetuple()))

    signature_origin = f"host: {config.ws_host}\n"
    signature_origin += f"date: {date}\n"
    signature_origin += f"GET {config.ws_path} HTTP/1.1"
    signature_sha = hmac.new(
        config.api_secret.encode("utf-8"),
        signature_origin.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    signature_b64 = base64.b64encode(signature_sha).decode("utf-8")
    authorization_origin = (
        f'api_key="{config.api_key}", algorithm="hmac-sha256", '
        f'headers="host date request-line", signature="{signature_b64}"'
    )
    authorization = base64.b64encode(authorization_origin.encode("utf-8")).decode("utf-8")

    params = {"authorization": authorization, "date": date, "host": config.ws_host}
    return f"{base_url}?{urlencode(params)}"


def _split_wav(audio_path: str, config: AsrConfig) -> list[str]:
    chunk_paths: list[str] = []
    with wave.open(audio_path, "rb") as wf:
        src_nchannels = wf.getnchannels()
        src_sampwidth = wf.getsampwidth()
        src_framerate = wf.getframerate()
        nframes = wf.getnframes()

        frames_per_chunk = int(config.chunk_duration_sec * src_framerate)
        overlap_frames = int(config.overlap_sec * src_framerate)
        if frames_per_chunk <= 0:
            raise ValueError("chunk_duration_sec 必须大于 0。")
        step = max(frames_per_chunk - overlap_frames, 1)

        start = 0
        while start < nframes:
            wf.setpos(start)
            chunk_frames = wf.readframes(frames_per_chunk)
            if not chunk_frames:
                break

            # 统一音频规格为 16k / 16bit / mono，降低 ASR 参数不匹配风险。
            pcm = chunk_frames
            if src_nchannels > 1:
                pcm = audioop.tomono(pcm, src_sampwidth, 0.5, 0.5)
            if src_framerate != config.sample_rate:
                pcm, _ = audioop.ratecv(
                    pcm,
                    src_sampwidth,
                    1,
                    src_framerate,
                    config.sample_rate,
                    None,
                )
            if src_sampwidth != 2:
                pcm = audioop.lin2lin(pcm, src_sampwidth, 2)

            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            tmp_path = tmp.name
            tmp.close()
            with wave.open(tmp_path, "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(config.sample_rate)
                out.writeframes(pcm)
            chunk_paths.append(tmp_path)
            start += step

    return chunk_paths


def _transcribe_single_wav(audio_path: str, config: AsrConfig) -> str:
    url = _auth_url(config)
    parts: list[str] = []
    error_holder: dict[str, str] = {}
    done = threading.Event()
    # 不使用 dwa=wpgs：动态修正会多次返回修订片段，简单拼接会导致转写文本重复循环。
    iat_params = {
        "domain": "slm",
        # MVP：为确保稳定跑通，固定使用中文模型参数。
        # （不依赖 ASR_LANGUAGE / ASR_ACCENT，避免不同音频/默认配置导致的 10404。）
        "language": "zh_cn",
        "accent": "mandarin",
        "result": {"encoding": "utf8", "compress": "raw", "format": "plain"},
    }

    def on_message(ws, message):
        try:
            payload = json.loads(message)
            code = payload.get("header", {}).get("code", -1)
            status = payload.get("header", {}).get("status")
            if code != 0:
                error_holder["error"] = f"ASR 返回错误码: {code}"
                ws.close()
                return
            result_payload = payload.get("payload", {}).get("result", {}).get("text")
            if result_payload:
                decoded = base64.b64decode(result_payload).decode("utf8")
                try:
                    text_json = json.loads(decoded)
                    for ws_item in text_json.get("ws", []):
                        for cw_item in ws_item.get("cw", []):
                            word = cw_item.get("w", "")
                            if word:
                                parts.append(word)
                except json.JSONDecodeError:
                    parts.append(decoded)
            if status == 2:
                done.set()
                ws.close()
        except Exception as exc:  # noqa: BLE001
            error_holder["error"] = f"解析 ASR 返回失败: {exc}"
            ws.close()

    def on_error(_, error):
        error_holder["error"] = f"ASR 连接异常: {error}"
        done.set()

    def on_close(_, __, ___):
        done.set()

    def on_open(ws):
        def run():
            status_first = 0
            status_continue = 1
            status_last = 2
            status = status_first

            with open(audio_path, "rb") as fp:
                while True:
                    buf = fp.read(config.frame_bytes)
                    if not buf:
                        status = status_last
                    audio_b64 = base64.b64encode(buf).decode("utf-8")
                    try:
                        if status == status_first:
                            data = {
                                "header": {"status": 0, "app_id": config.app_id},
                                "parameter": {"iat": iat_params},
                                "payload": {
                                    "audio": {
                                        "audio": audio_b64,
                                    "sample_rate": config.sample_rate,
                                        "encoding": config.encoding,
                                    }
                                },
                            }
                            ws.send(json.dumps(data))
                            status = status_continue
                        elif status == status_continue:
                            data = {
                                "header": {"status": 1, "app_id": config.app_id},
                                "parameter": {"iat": iat_params},
                                "payload": {
                                    "audio": {
                                        "audio": audio_b64,
                                        "sample_rate": config.sample_rate,
                                        "encoding": config.encoding,
                                    }
                                },
                            }
                            ws.send(json.dumps(data))
                        else:
                            data = {
                                "header": {"status": 2, "app_id": config.app_id},
                                "parameter": {"iat": iat_params},
                                "payload": {
                                    "audio": {
                                        "audio": audio_b64,
                                        "sample_rate": config.sample_rate,
                                        "encoding": config.encoding,
                                    }
                                },
                            }
                            ws.send(json.dumps(data))
                            break
                    except Exception as exc:  # noqa: BLE001
                        error_holder["error"] = f"发送音频帧失败: {exc}"
                        done.set()
                        break
                    time.sleep(config.frame_interval_sec)

        threading.Thread(target=run, daemon=True).start()

    ws = websocket.WebSocketApp(url, on_message=on_message, on_error=on_error, on_close=on_close)
    ws.on_open = on_open

    thread = threading.Thread(
        target=lambda: ws.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE}),
        daemon=True,
    )
    thread.start()
    done.wait(timeout=config.timeout_sec)
    if "error" in error_holder:
        raise RuntimeError(error_holder["error"])
    if not done.is_set():
        raise TimeoutError("ASR 转写超时。")
    return "".join(parts).strip()


def _transcribe_chunk_with_retry(chunk_path: str, config: AsrConfig) -> str:
    last_error: Exception | None = None
    for attempt in range(config.retries_per_chunk + 1):
        try:
            return _transcribe_single_wav(chunk_path, config)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < config.retries_per_chunk:
                time.sleep(config.retry_backoff_sec * (attempt + 1))
    raise RuntimeError(f"片段转写失败: {last_error}") from last_error


def transcribe_audio(audio_path: str) -> dict:
    """
    将 WAV 音频切片后逐片调用 ASR 并合并文本。
    上游应已通过 audio_preprocess 将 MP3/M4A/PCM 转为 WAV。
    """
    if not audio_path.lower().endswith(".wav"):
        raise ValueError("ASR 入口仅接受 WAV 路径，请检查音频预处理。")

    config = AsrConfig.from_env()
    chunk_paths = _split_wav(audio_path, config)
    merged_parts: list[str] = [""] * len(chunk_paths)

    try:
        if not chunk_paths:
            return {"text": "", "language": "zh", "segments": []}

        workers = min(config.max_workers, len(chunk_paths))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_idx = {
                executor.submit(_transcribe_chunk_with_retry, chunk_path, config): idx
                for idx, chunk_path in enumerate(chunk_paths)
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                merged_parts[idx] = future.result()
    finally:
        for path in chunk_paths:
            try:
                os.remove(path)
            except OSError:
                pass

    full_text = "".join(part for part in merged_parts if part)
    return {"text": full_text, "language": "zh", "segments": []}
