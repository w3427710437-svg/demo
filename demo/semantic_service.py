import json
import os
import re
import hashlib
from typing import Any

from openai import OpenAI

PROMPT_VERSION = "v5_prompt_rubric_10types_hardneg"

# 准确率优先：阈值偏严格，可通过环境变量覆盖
DEFAULT_MENTIONED_CONF_THRESHOLD = 0.75


def _cache_file_path() -> str:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    cache_dir = os.path.join(base_dir, ".cache")
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, "semantic_cache.json")


def _read_cache() -> dict[str, Any]:
    path = _cache_file_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _write_cache(data: dict[str, Any]) -> None:
    path = _cache_file_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def _alias_dict_path() -> str:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, "alias_dict.json")


def _load_alias_dict() -> dict[str, list[str]]:
    path = _alias_dict_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            return {}
        out: dict[str, list[str]] = {}
        for k, v in raw.items():
            if isinstance(k, str) and isinstance(v, list):
                out[k] = [x for x in v if isinstance(x, str) and x.strip()]
        return out
    except Exception:  # noqa: BLE001
        return {}


def _apply_alias_shortcut(transcript_text: str, keywords: list[str], mentioned_flags: list[bool]) -> None:
    alias_map = _load_alias_dict()
    if not alias_map:
        return
    for i, kw in enumerate(keywords):
        if mentioned_flags[i]:
            continue
        aliases = alias_map.get(kw, [])
        for alias in aliases:
            if alias in transcript_text:
                mentioned_flags[i] = True
                break


def _split_sentences(text: str) -> list[str]:
    """
    基于中文标点切句（保留句末标点），用于减少 LLM 看到的无关上下文。
    """
    if not text:
        return []
    # 匹配“非标点+可选标点”，尽量保持一句一个单元
    sentences = re.findall(r"[^。！？!?；;]+[。！？!?；;]?", text)
    return [s.strip() for s in sentences if s and s.strip()]


def _chunk_text(text: str, window_sentences: int = 6, overlap_sentences: int = 1) -> list[str]:
    """
    按“句窗”切 chunk：更接近 StructBERT 的句对语义匹配工作方式。
    """
    sentences = _split_sentences(text)
    if not sentences:
        return []
    chunks: list[str] = []
    step = max(window_sentences - overlap_sentences, 1)
    for start in range(0, len(sentences), step):
        end = min(start + window_sentences, len(sentences))
        chunk = "".join(sentences[start:end])
        if chunk.strip():
            chunks.append(chunk)
        if end >= len(sentences):
            break
    return chunks


def _extract_json(text: str) -> dict[str, Any]:
    match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    raw = match.group(1).strip() if match else text.strip()
    return json.loads(raw)


def _build_prompt(chunk: str, remaining_keywords: list[str]) -> tuple[str, str]:
    system = (
        "你是一个信息抽取助手。任务：判断文本片段中，哪些“关键词实体”在语义上被提及（Mentioned）。"
        "你必须严格按证据选择，不能凭空猜测。"
        "你只能在满足以下“可映射证据”时才判 mentioned："
        "A. 显式点名/别名：关键词原字面或别名/简称/常见等价写法/中英互译/音译/外文译名出现。"
        "B. 翻译/缩写：同一实体在另一语言或缩写形式出现（如 VAT/IBM 等）。"
        "C. 比较/对比/替代/优劣：同一句表达对多个实体的比较、替代、纠正（例如“不是X而是Y”“不用X改用Y”“X比Y更好”），则涉及的所有关键词实体都算提及。"
        "D. 否定/反问/拒绝也算提及：围绕该实体做排除/限制/纠正时，相关关键词实体仍算提及（X与替换对象 Y 都要算）。"
        "E. 指代（代词/称呼/人群视角）：诸如“它/那个/这家/那家/先把那个账户”等，只有在片段内能追溯到某个关键词实体（明确前指或同一对话线索）时才允许选；否则不要选。"
        "F. 总结/归纳（长话打包成一句）：只有当总结句把前文的“主题”明确落到某个实体关键词时，才允许选（例如“最后这块就是企业开户/社保相关我们都整理好了”）；"
        "当总结只是泛泛说“这些/后面/再看结果”但无法映射到某个具体关键词实体时，不要选。"
        "G. 抽象词/同一语义簇变体（更具体/更抽象）："
        "只有当抽象词（如“机构级别/票种体系/五险一金主题”）与某个关键词实体存在稳定语义映射（可判定属于同一实体范畴）时才允许选；"
        "若太泛（如“服务/平台/系统/流程/材料”而无法唯一映射），不要选。"
        "H. 功能/结果/流程证据：只有当描述的“功能组合/字段/结果”高度指向某个关键词实体（与该实体语义簇一致且可映射），才可以选；"
        "否则不要用“流程/材料/审核/字段”等泛化词去猜测具体关键词。"
        "I. 枚举/并列主题组：若关键词实体被并列在同一语义主题里（如“五险一金”覆盖社保语义），且主题与具体关键词实体可映射，则允许选。"
        "J. 不能确定就空：宁可漏判也不要误选。"
        "输出只允许 JSON（不要使用 Markdown 代码块，也不要输出额外文字）。"
    )
    indexed_keywords = "\n".join(f"{i}. {kw}" for i, kw in enumerate(remaining_keywords))
    user = (
        "请阅读下面文本片段，判断其中哪些关键词实体在语义上被提及（不需要关键词逐字出现）。"
        "注意：如果证据不足以把片段内容明确映射到某个关键词实体，则不要选该关键词。"
        "可把每条判定理解为：对每个关键词实体，是否存在“可映射证据（A~H）”。"
        "hard negative（必须避免误选）："
        "1) 关键词=企业开户，文本仅说“办理流程需要材料，审核要时间” -> 不要选（太泛、无法映射到具体实体）。"
        "2) 关键词=社保，文本仅说“这件事先准备资料，后面再看结果” -> 通常不要选（没有社保语义证据）。"
        "3) 关键词=Apple，文本说“手机品牌都差不多，主要看体验” -> 不要选（泛化到手机品牌）。"
        "4) 指代：看到“那家/那个账户/它”但片段内无法追溯到给定关键词实体 -> 不要选。"
        "5) 总结句：只有“这些/那块/后面”但无法明确映射到某个关键词实体 -> 不要选。"
        "输出每个被提及关键词实体的置信度 confidence（0~1）。置信度建议："
        " - 0.85~1.0：明确点名/强指代/几乎无歧义映射；"
        " - 0.7~0.85：同义/近义/翻译/替代，仍有清晰映射；"
        " - 0.55~0.7：代指/总结/主题组，映射较明确但证据不够强；"
        " - <0.55：宁可漏判，不要输出。"
        "输出格式严格为：{\"items\": [{\"index\": 0, \"confidence\": 0.83}]}\n\n"
        f"关键词列表（下标从0开始）：\n{indexed_keywords}\n\n"
        f"文本片段：\n{chunk}"
    )
    return system, user


def score_mentioned(transcript_text: str, keywords: list[str], touched_flags: list[bool]) -> dict:
    mentioned_flags = touched_flags[:]
    # ROI优化第1层：别名字典快捷命中，减少LLM调用量
    _apply_alias_shortcut(transcript_text, keywords, mentioned_flags)

    mentioned_confidences = [1.0 if f else 0.0 for f in mentioned_flags]
    mention_threshold = float(os.getenv("MENTIONED_CONFIDENCE_THRESHOLD", str(DEFAULT_MENTIONED_CONF_THRESHOLD)))
    mention_threshold = max(0.0, min(1.0, mention_threshold))

    remaining_indices = [i for i, f in enumerate(mentioned_flags) if not f]
    if not remaining_indices:
        return {
            "total_mentioned": sum(1 for x in mentioned_flags if x),
            "mentioned_flags": mentioned_flags,
            "mentioned_confidences": mentioned_confidences,
            "warning": "",
        }

    api_key = os.getenv("LLM_API_KEY", "").strip()
    base_url = os.getenv("LLM_BASE_URL", "").strip()
    model = os.getenv("LLM_MODEL", "").strip() or "qwen-plus"
    if not api_key:
        return {
            "total_mentioned": sum(1 for x in mentioned_flags if x),
            "mentioned_flags": mentioned_flags,
            "warning": "未配置 LLM_API_KEY，语义提及暂按精准触达直通结果返回。",
        }

    client = OpenAI(api_key=api_key, base_url=base_url or None)
    remaining_keywords = [keywords[i] for i in remaining_indices]
    chunks = _chunk_text(transcript_text)
    if not chunks:
        return {
            "total_mentioned": sum(1 for x in mentioned_flags if x),
            "mentioned_flags": mentioned_flags,
            "mentioned_confidences": mentioned_confidences,
            "warning": "",
        }

    # ROI优化第2层：对同样输入做缓存，避免重复调用LLM
    cache_key_payload = {
        "model": model,
        "remaining_keywords": remaining_keywords,
        "chunks": chunks,
        "prompt_version": PROMPT_VERSION,
    }
    cache_key = hashlib.sha256(
        json.dumps(cache_key_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    cache = _read_cache()
    cached_value = cache.get(cache_key)
    if isinstance(cached_value, list):
        # cached_value 结构：[(local_idx:int, confidence:float), ...]
        for item in cached_value:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                local_idx, conf = item
                if isinstance(local_idx, int) and 0 <= local_idx < len(remaining_indices):
                    try:
                        conf_f = float(conf)
                    except Exception:
                        conf_f = 0.0
                    orig_idx = remaining_indices[local_idx]
                    mentioned_confidences[orig_idx] = max(mentioned_confidences[orig_idx], conf_f)
        mentioned_flags = [c >= mention_threshold for c in mentioned_confidences]
        return {
            "total_mentioned": sum(1 for x in mentioned_flags if x),
            "mentioned_flags": mentioned_flags,
            "mentioned_confidences": mentioned_confidences,
            "warning": "",
        }

    try:
        # 本轮LLM给出的 remaining keyword 的最大 confidence
        llm_best_confs: dict[int, float] = {}
        for chunk in chunks:
            system_prompt, user_prompt = _build_prompt(chunk, remaining_keywords)
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0,
            )
            content = resp.choices[0].message.content or ""
            data = _extract_json(content)

            # 兼容旧格式：{"mentioned_indices":[...]}（置信度置为0.9）
            idxs = data.get("mentioned_indices")
            if isinstance(idxs, list):
                for local_idx in idxs:
                    if isinstance(local_idx, int) and 0 <= local_idx < len(remaining_indices):
                        llm_best_confs[local_idx] = max(llm_best_confs.get(local_idx, 0.0), 0.9)
            else:
                items = data.get("items", [])
                if not isinstance(items, list):
                    continue
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    local_idx = it.get("index")
                    conf = it.get("confidence")
                    if isinstance(local_idx, int) and 0 <= local_idx < len(remaining_indices):
                        try:
                            conf_f = float(conf)
                        except Exception:
                            conf_f = 0.0
                        conf_f = max(0.0, min(1.0, conf_f))
                        llm_best_confs[local_idx] = max(llm_best_confs.get(local_idx, 0.0), conf_f)

        # 写回 best confidence
        for local_idx, conf in llm_best_confs.items():
            orig_idx = remaining_indices[local_idx]
            mentioned_confidences[orig_idx] = max(mentioned_confidences[orig_idx], conf)

        mentioned_flags = [c >= mention_threshold for c in mentioned_confidences]

        cache[cache_key] = sorted([(k, v) for k, v in llm_best_confs.items()], key=lambda x: x[0])
        _write_cache(cache)
    except Exception as exc:  # noqa: BLE001
        return {
            "total_mentioned": sum(1 for x in mentioned_flags if x),
            "mentioned_flags": mentioned_flags,
            "mentioned_confidences": mentioned_confidences,
            "warning": f"LLM 调用失败，语义提及回退为直通结果：{exc}",
        }

    return {
        "total_mentioned": sum(1 for x in mentioned_flags if x),
        "mentioned_flags": mentioned_flags,
        "mentioned_confidences": mentioned_confidences,
        "warning": "",
    }

