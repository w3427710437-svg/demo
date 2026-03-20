# Streamlit Cloud 部署指南（最快拿公网链接）

目标：把 `demo/app.py` 部署为可公开访问的链接，给他人直接试用。

---

## 1. 部署前准备

1. 确保本地可运行：
   ```bash
   cd demo
   pip install -r requirements.txt
   streamlit run app.py
   ```
2. 确保代码在 GitHub 仓库（建议仓库根目录包含 `demo/` 文件夹）。
3. 确保不要提交真实 `.env`（密钥只放 Streamlit Secrets）。

---

## 2. 在 Streamlit Cloud 创建应用

1. 打开 [https://share.streamlit.io/](https://share.streamlit.io/)
2. 使用 GitHub 账号登录
3. 点击 `New app`
4. 填写：
   - Repository: 你的仓库
   - Branch: `main`（或你实际分支）
   - Main file path: `demo/app.py`
5. 点击 `Advanced settings`，先不填 Secrets 也可以，后面再补
6. 点击 `Deploy`

---

## 3. 配置 Secrets（关键）

部署后进入应用页面右下角或设置页，打开 `Secrets`，粘贴以下模板并替换真实值：

```toml
ASR_APP_ID="你的ASR_APP_ID"
ASR_API_KEY="你的ASR_API_KEY"
ASR_API_SECRET="你的ASR_API_SECRET"

LLM_API_KEY="你的LLM_API_KEY"
LLM_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
LLM_MODEL="qwen-plus"

MENTIONED_CONFIDENCE_THRESHOLD="0.75"

ASR_MAX_WORKERS="10"
ASR_CHUNK_DURATION_SEC="60"
ASR_OVERLAP_SEC="0.5"
ASR_FRAME_INTERVAL_SEC="0.02"
ASR_RETRIES_PER_CHUNK="2"
ASR_RETRY_BACKOFF_SEC="0.5"
ASR_TIMEOUT_SEC="120"
```

保存后应用会自动重启。

---

## 4. 发送试用链接

部署成功后，你会得到一个公网地址，格式类似：

- `https://<your-app-name>.streamlit.app/`

把这个链接直接发给试用人即可。

---

## 5. 上线前快速自检（建议）

1. 打开公网链接，页面可正常加载；
2. 用一段短 WAV（10~30 秒）先做冒烟；
3. 关键词少量输入（5~10 个）确认结果正常；
4. 再测 20 分钟样本；
5. 若超时或变慢，先下调 `ASR_MAX_WORKERS` 到 `6` 或 `8`。

---

## 6. 常见问题

### Q1: 部署后报缺少依赖
- 检查 `demo/requirements.txt` 是否包含全部依赖；
- 重新 Deploy 或点击 Reboot。

### Q2: 语义提及一直退化为触达
- 通常是 `LLM_API_KEY` 未配置或错误；
- 检查 Secrets 并重启应用。

### Q3: MP3/M4A 转码失败
- 云端环境可能没有 ffmpeg；
- 先让试用只上传 WAV，或后续改为项目内置 ffmpeg 方案。

### Q4: ASR 太慢
- 先确认 `ASR_MAX_WORKERS` 生效；
- 适当调大 `ASR_CHUNK_DURATION_SEC`，调小 `ASR_FRAME_INTERVAL_SEC`；
- 并发过高若触发限流，再回调到 `6~8`。

### Q5: 云上比本地明显慢，如何接近本地体验
- 建议启用“远端 ASR 后端”架构：将 ASR 部署在你可控、同地域云主机；
- Streamlit 端只需在 Secrets 增加：
  - `ASR_BACKEND_URL`
  - `ASR_BACKEND_TOKEN`（可选）
  - `ASR_BACKEND_TIMEOUT_SEC`（可选）
- 配置后，应用会优先调用远端 ASR；未配置则自动回退本地 ASR 逻辑。
- 远端后端参考实现见：`demo/remote_asr_backend/README.md`
