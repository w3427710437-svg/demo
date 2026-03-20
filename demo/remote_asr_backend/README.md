# Remote ASR Backend

该服务用于把 ASR 从 Streamlit Cloud 分离到你自己的云主机，以获得更稳定、更快的转写体验。

## 1. 安装依赖

```bash
cd demo/remote_asr_backend
pip install -r requirements.txt
```

## 2. 关键环境变量

必填（与主项目同一套讯飞参数）：
- `ASR_APP_ID`
- `ASR_API_KEY`
- `ASR_API_SECRET`

可选：
- `ASR_BACKEND_TOKEN`（若配置则要求 Bearer Token）
- `ASR_MAX_WORKERS`
- `ASR_CHUNK_DURATION_SEC`
- `ASR_OVERLAP_SEC`
- `ASR_FRAME_INTERVAL_SEC`
- `ASR_FRAME_INTERVAL_SCALE`
- `ASR_RETRIES_PER_CHUNK`
- `ASR_RETRY_BACKOFF_SEC`
- `ASR_TIMEOUT_SEC`

## 3. 启动

在仓库根目录启动（确保可导入 `asr_service.py`）：

```bash
uvicorn demo.remote_asr_backend.app:app --host 0.0.0.0 --port 8000
```

健康检查：

```bash
curl http://127.0.0.1:8000/healthz
```

## 4. 接口协议

### POST `/transcribe`

Request JSON:

```json
{
  "audio_base64": "<wav-base64>",
  "filename": "upload.wav",
  "content_type": "audio/wav"
}
```

Response JSON:

```json
{
  "text": "转写全文",
  "language": "zh",
  "segments": []
}
```

## 5. Streamlit 端配置

在 Streamlit Cloud Secrets 配置：

```toml
ASR_BACKEND_URL="https://<your-domain>/transcribe"
ASR_BACKEND_TOKEN="your_token"
ASR_BACKEND_TIMEOUT_SEC="300"
```

配置后主应用会优先调用远端 ASR；未配置则回退本地 ASR。
