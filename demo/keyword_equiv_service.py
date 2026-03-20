import hashlib
import json
import os
import re
from typing import Any

from openai import OpenAI


PROMPT_VERSION = "touched_crosslingual_alias_v4_more_candidates"

def _cache_file_path() -> str:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    cache_dir = os.path.join(base_dir, ".cache")
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, "keyword_equiv_cache.json")


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


def _is_latin(s: str) -> bool:
    return bool(re.search(r"[A-Za-z]", s or ""))


def _normalize_keywords_input(keywords: list[str]) -> list[str]:
    out: list[str] = []
    for kw in keywords:
        kw = (kw or "").strip()
        if kw:
            out.append(kw)
    return out


def _expand_aliases_llm(keywords: list[str], model: str, base_url: str | None, api_key: str) -> dict[str, list[str]]:
    client = OpenAI(api_key=api_key, base_url=base_url)
    indexed = list(enumerate(keywords))

    system_prompt = (
        "你是一个跨语言实体翻译助手。"
        "目标：为每个关键词生成“用于严格字符串匹配”的跨语言翻译候选。"
        "强约束：必须输出跨语言翻译项；不能只输出原词。"
        "要求："
        "1) 对每个关键词输出：items[0] = 原关键词；items[1] = 对语言的翻译（如果输入为英文则给中文；如果输入为中文则给英文）。"
        "2) 语言约束："
        "   - 若 keywords 含 A-Z/a-z，则 items[1] 必须包含至少一个中文字符（\\u4e00-\\u9fff）。"
        "   - 若 keywords 不含 A-Z/a-z，则 items[1] 必须包含至少一个英文字母（A-Za-z）。"
        "3) 若确实无法翻译，items[1] 也必须给出最可能的常见写法/音译；但不得等于 items[0]。"
        "4) items 总长度：2~6（最多 6）。"
        "5) items 总长度：2~6（items[0] 原词、items[1] 必须为跨语言翻译，items[2..] 可选常见写法/同义实体名）。"
        "6) 只输出严格 JSON，格式必须为：{\"aliases\":[{\"index\":0,\"items\":[\"...\"],\"note\":\"...\"}, ...]}。"
    )

    user_prompt = {
        "task": "aliases",
        "keywords": [
            {
                "index": i,
                "keyword": kw,
                "needs_translate_language": "zh" if _is_latin(kw) else "en",
            }
            for i, kw in indexed
        ],
        "hint": (
            "请尽量输出常见写法，适配 ASR 转写文本可能的噪声（例如把空格/符号保留为与原意接近的形式）。"
            "但必须满足强约束：items[1] 的语言类型正确，且与原词不同。"
        ),
    }

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_prompt, ensure_ascii=False)},
        ],
        temperature=0,
        max_tokens=1200,
    )
    content = resp.choices[0].message.content or ""

    # 兼容：有些模型可能会包 json code fence
    m = re.search(r"```json\s*(.*?)\s*```", content, re.DOTALL)
    raw = m.group(1).strip() if m else content.strip()
    data = json.loads(raw)

    # 期望：{ "aliases": [ {"index":0,"items":["..."]}, ... ] }
    aliases_list = data.get("aliases", [])
    out: dict[str, list[str]] = {}
    for item in aliases_list:
        if not isinstance(item, dict):
            continue
        idx = item.get("index")
        items = item.get("items", [])
        if isinstance(idx, int) and 0 <= idx < len(keywords) and isinstance(items, list):
            kw = keywords[idx]
            cleaned = []
            for x in items:
                if isinstance(x, str):
                    x2 = x.strip()
                    if x2 and x2 not in cleaned:
                        cleaned.append(x2)
            # 强制保证 items[0] 是原关键词
            if not cleaned:
                cleaned = [kw]
            if cleaned[0] != kw:
                cleaned = [kw] + [x for x in cleaned if x != kw]
            out[kw] = cleaned[:6]

    # 若模型漏掉某些关键词，回填为空列表
    for kw in keywords:
        out.setdefault(kw, [])
    return out


def expand_keyword_aliases(
    keywords: list[str],
    *,
    max_expand_keywords: int = 30,
) -> dict[str, list[str]]:
    """
    为一组关键词生成跨语言别名（含缓存）。
    返回值：{keyword: [alias1, alias2, ...]}
    """
    keywords = _normalize_keywords_input(keywords)
    if not keywords:
        return {}

    # 读 env
    api_key = os.getenv("LLM_API_KEY", "").strip()
    base_url = os.getenv("LLM_BASE_URL", "").strip() or None
    model = os.getenv("LLM_MODEL", "").strip() or "qwen-plus"

    if not api_key:
        # 无LLM配置就退化：只用原词
        return {kw: [kw] for kw in keywords}

    if len(keywords) > max_expand_keywords:
        # ROI 控制：超出就只返回自己，避免一次性扩太多
        reduced = keywords[:max_expand_keywords]
        return {kw: [kw] for kw in reduced} | {kw: [] for kw in keywords[max_expand_keywords:]}

    payload = {"model": model, "keywords": keywords, "prompt_version": PROMPT_VERSION}
    cache_key = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    cache = _read_cache()
    cached = cache.get(cache_key)
    if isinstance(cached, dict):
        # 确保返回值含每个关键词至少一个项（原词）
        for kw in keywords:
            cached.setdefault(kw, [])
            if kw not in cached[kw]:
                cached[kw].insert(0, kw)
        return cached

    aliases = _expand_aliases_llm(keywords=keywords, model=model, base_url=base_url, api_key=api_key)
    # 填充原词，确保一定可匹配
    for kw in keywords:
        aliases.setdefault(kw, [])
        if kw not in aliases[kw]:
            aliases[kw].insert(0, kw)

    cache[cache_key] = aliases
    _write_cache(cache)
    return aliases

