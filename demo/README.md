# 企业AI工作流 Demo PRD（准确触达与语义提及）

本文档基于 `问题.txt` 中的原始题目、`企业AI工作流_准确触达与语义提及_实现计划.md` 的方案设计，以及当前 `demo` 代码的实际落地状态编写，作为当前版本的产品需求文档（PRD）。

---

## 1. 需求分析

### 1.1 业务背景
企业在语音质检、销售复盘、客服合规、培训评估等场景中，常需要快速回答两个问题：
- 说话内容是否“准确触达”指定关键词（字面是否出现）；
- 说话内容是否“语义提及”指定主题（哪怕没有逐字出现）。

### 1.2 题目要求（问题定义）
输入：
- 用户语音输入一段话，时长不超过 30 分钟；
- 用户给定关键词列表，`n <= 100`，每个关键词不超过 10 个字。

输出：
- **精准触达**：每个关键词是否被准确触达，并给出总分；
- **语义提及**：每个关键词语义上是否被提及，并给出总分。

工程要求：
- 使用大模型，保证准确率并提升 ROI（成本收益比）；
- 最好具备可演示 Demo；
- 需要多语言友好能力。

### 1.3 本项目目标
在可控成本下交付一个可运行 Demo，完成“语音 -> 双评分”的端到端闭环，支持真实音频输入、关键词解析、结果导出和调试排查。

---

## 2. 方案设计（分层架构）

### 2.1 总体流程
1. 用户上传音频或网页录音，输入关键词；
2. 音频预处理统一为 ASR 可识别 WAV；
3. ASR 转写得到 transcript；
4. 精准触达层计算 `touched_flags` 与 `total_touched`；
5. 语义提及层计算 `mentioned_flags` 与 `total_mentioned`；
6. 页面返回两个总分，并支持 JSON/CSV 导出。

### 2.2 分层职责
- **交互层（UI）**：输入校验、任务进度、结果可视化、导出；
- **编排层（Analyzer）**：统一串联 ASR、触达、语义提及；
- **ASR层**：长音频切片、并发转写、失败重试、顺序合并；
- **规则层（Touched）**：AC 自动机做严格字面匹配；
- **语义层（Mentioned）**：规则直通 + 别名词典 + LLM 分块判断；
- **ROI层（优化策略）**：缓存、分层降本、阈值控制、按需调用。

### 2.3 关键设计原则
- 规则任务尽量不用 LLM，降低成本；
- 语义任务只处理“剩余疑难关键词”，提高每次 LLM 调用价值；
- 保持结果可解释：每层都有明确输入/输出；
- 兼顾准确率和吞吐：在 ASR 侧引入并发切片策略。

---

## 3. 具体实现（基于当前代码）

### 3.1 交互与编排
- `app.py`
  - 支持网页录音和文件上传（WAV/MP3/M4A/PCM）；
  - 校验规则：30 分钟上限、关键词 <=100、单词长度 <=10；
  - 展示两个核心指标：`精准触达总分`、`语义提及总分`；
  - 支持导出 `JSON/CSV`。
- `analyzer_service.py`
  - 统一入口 `analyze_audio_and_keywords(...)`；
  - 串联：`audio_preprocess -> asr_service -> touched_service -> semantic_service`；
  - 提供耗时与告警输出，便于调试与运维。

### 3.2 ASR（长音频并发转写）
- `audio_preprocess.py`
  - 将上传音频统一转为 16k 单声道 16bit WAV（MP3/M4A/PCM 可转）。
- `asr_service.py`
  - WebSocket 鉴权 + 分帧发送；
  - 长音频按 chunk 切片；
  - 引入并发转写（线程池），支持 `ASR_MAX_WORKERS`（当前可配置到 10）；
  - 每片支持重试与退避；
  - 并发完成后按分片索引顺序合并，保证文本时序一致。

### 3.3 精准触达实现
- `touched_service.py`
  - 文本归一化后使用 Aho-Corasick 自动机进行多关键词匹配；
  - 输出 `touched_flags` 和 `total_touched`；
  - 支持别名补匹配（用于跨语言/别名补触达）。

### 3.4 语义提及实现
- `semantic_service.py`
  - Mentioned 判定采用分层：
    1) `touched=true` 直通 `mentioned=true`；
    2) `alias_dict.json` 命中直通；
    3) 剩余关键词交给 LLM；
  - Transcript 按句窗分块，降低无关上下文干扰；
  - LLM 输出结构化 JSON（index + confidence）；
  - 置信度阈值 `MENTIONED_CONFIDENCE_THRESHOLD` 控制精确率/召回率平衡；
  - 结果缓存到 `demo/.cache/semantic_cache.json`，避免重复成本。

### 3.5 跨语言补齐
- `keyword_equiv_service.py`
  - 对未命中关键词做跨语言别名扩展（可缓存）；
  - 仅在“关键词语言与语音语言不一致或混杂”时触发，避免无意义调用。

---

## 4. 关键能力提升（准确率与 ROI）

### 4.1 准确率提升
- **口径拆分**：精准触达与语义提及分离，避免单一模型混判；
- **多层证据**：字面命中、别名词典、语义推理三层融合；
- **置信度阈值**：通过阈值抑制语义误报；
- **长文本分块**：按句窗切块，减少上下文噪声；
- **负样本意识**：在提示词中加入 hard negative 约束，降低过度匹配。

### 4.2 ROI 提升
- **规则优先**：touched 场景全程不调用 LLM；
- **分层直通**：命中词与别名词先过滤，减少 LLM 请求量；
- **缓存复用**：同输入复用语义结果，重复测试零增量成本；
- **按需跨语言**：仅在语言不一致时做别名扩展；
- **ASR 并发**：把长音频识别总耗时从近实时串行转为并发压缩，改善用户等待体验。

---

## 5. 数据评测（测试集设计与样本）

### 5.1 测试目标
验证系统能否稳定区分三类结果：
- 准确触及（应 touched=true）；
- 语义提及（应 mentioned=true 且可不 touched）；
- 不应提及（应 mentioned=false）。

### 5.2 评测输入数据
- 音频样本：`demo/281a3d09efcb45d9a9bca21f37dbced0.mp3`（约 20 分钟）
- 文本参考：`demo/文本.txt`
- 关键词集：`demo/关键词.txt`
- 扩展测试集（文本版）：`demo/测试集_关键词.txt`

### 5.3 测试集结构
当前测试集按 4 类组织：
- 准确触达（核心）
- 语义提及
- 未提及（强负样本）
- 迷惑性强但未提及（hard negative）

### 5.4 评测结论口径（用于验收）
- **流程可跑通**：端到端无阻断报错；
- **准确触达可解释**：命中结果与原文一致；
- **语义提及可控**：在阈值下误报可接受；
- **时延可接受**：并发 ASR 后较串行明显下降。

---

## 6. 部署上线（试用入口）

### 6.1 本地试用链接
启动后访问：
- [http://localhost:8501](http://localhost:8501)

启动命令：
```bash
cd demo
pip install -r requirements.txt
streamlit run app.py
```

### 6.2 局域网试用链接
```bash
streamlit run app.py --server.address 0.0.0.0 --server.port 8501
```
访问方式：
- `http://<你的IP>:8501`

### 6.3 公网上线建议
- 方式：云主机 + `streamlit` 常驻 + `nginx` 反向代理 + HTTPS；
- 密钥使用环境变量或平台 Secrets 注入；
- 上线前完成限流、鉴权、日志与告警。

### 6.4 远端 ASR 加速（推荐）
当 Streamlit Cloud 上 ASR 明显慢于本地时，建议把 ASR 放到你自己的同地域后端服务，Streamlit 只做前端展示与结果计算编排。

可选环境变量：
- `ASR_BACKEND_URL`：远端 ASR HTTP 接口地址（配置后优先走远端）
- `ASR_BACKEND_TOKEN`：远端接口鉴权 token（可选）
- `ASR_BACKEND_TIMEOUT_SEC`：远端调用超时秒数（默认 300）

接口约定（POST JSON）：
```json
{
  "audio_base64": "<wav-base64>",
  "filename": "xxx.wav",
  "content_type": "audio/wav"
}
```

返回约定（JSON）：
```json
{
  "text": "转写全文",
  "language": "zh",
  "segments": []
}
```

---

## 附录A：环境变量（当前版本）

必填：
- `ASR_APP_ID`
- `ASR_API_KEY`
- `ASR_API_SECRET`
- `LLM_API_KEY`（若不填则语义提及退化）

常用可调：
- `LLM_BASE_URL`
- `LLM_MODEL`
- `MENTIONED_CONFIDENCE_THRESHOLD`
- `ASR_MAX_WORKERS`（建议先设 10）
- `ASR_CHUNK_DURATION_SEC`
- `ASR_OVERLAP_SEC`
- `ASR_FRAME_INTERVAL_SEC`
- `ASR_RETRIES_PER_CHUNK`
- `ASR_RETRY_BACKOFF_SEC`
- `ASR_TIMEOUT_SEC`

---

## 附录B：当前版本范围说明

- 当前文档为 Demo 阶段 PRD，目标是“可运行、可解释、可评测、可迭代”；
- 生产级能力（账号体系、权限、审计、监控、多租户等）不在本期范围内；
- 后续可在本 PRD 基础上扩展为正式版本的 MRD/PRD + 技术设计说明。
