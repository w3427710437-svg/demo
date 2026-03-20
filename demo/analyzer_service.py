import os
import tempfile
import time
import wave
import re
from typing import Any

from asr_service import transcribe_audio
from audio_preprocess import write_upload_to_wav
from keyword_equiv_service import expand_keyword_aliases
from semantic_service import score_mentioned
from touched_service import score_touched


def _normalize_transcript_text(text: str) -> str:
    """
    轻量清洗 ASR 文本：
    - 去掉首尾空白
    - 折叠连续空白
    """
    return " ".join(text.strip().split())


def _validate_keywords(keywords: list[str]) -> None:
    if not keywords:
        raise ValueError("关键词不能为空。")
    if len(keywords) > 100:
        raise ValueError("关键词数量不能超过100，超出后无法执行。")
    too_long = [kw for kw in keywords if len(kw) > 10]
    if too_long:
        sample = "、".join(too_long[:3])
        raise ValueError(f"存在超过10个字的关键词（例如：{sample}），无法执行。")


def _wav_duration_seconds(wav_path: str) -> float:
    with wave.open(wav_path, "rb") as wf:
        fr = wf.getframerate()
        nf = wf.getnframes()
    return 0.0 if fr <= 0 else nf / fr


def _detect_text_language(text: str) -> str:
    """
    简化语言检测：
    - 仅含中文字符 -> zh
    - 仅含拉丁字母 -> en
    - 中英混杂 -> mixed
    - 两者都没有 -> unknown
    """
    t = text or ""
    has_cjk = bool(re.search(r"[\u4e00-\u9fff]", t))
    has_latin = bool(re.search(r"[A-Za-z]", t))
    if has_cjk and has_latin:
        return "mixed"
    if has_cjk:
        return "zh"
    if has_latin:
        return "en"
    return "unknown"


def analyze_audio_and_keywords(
    audio_bytes: bytes,
    keywords: list[str],
    upload_filename: str | None = None,
) -> dict[str, Any]:
    """
    T7 统一服务入口：串联 ASR -> touched -> mentioned。
    """
    start_ts = time.time()
    _validate_keywords(keywords)

    wav_path, cleanup_paths = write_upload_to_wav(audio_bytes, upload_filename)

    try:
        duration_sec = _wav_duration_seconds(wav_path)
        if duration_sec > 1800:
            raise ValueError("语音时长超过30分钟，无法执行。")
        asr_result = transcribe_audio(wav_path)
    finally:
        for path in cleanup_paths:
            try:
                os.remove(path)
            except OSError:
                pass

    transcript_text = _normalize_transcript_text(asr_result.get("text", ""))
    touched_result = score_touched(transcript_text, keywords)
    touched_flags = touched_result["touched_flags"][:]

    # 跨语言准确触达（Touched）：
    # 按 ROI 口径：
    # 1) 如果语音文本与关键词都为同一种语言（且均为 zh 或 en），则不翻译，直接用第一次严格匹配结果。
    # 2) 否则（中英混杂或语言不一致），对第一次未命中的关键词做“翻译/别名扩展”，再做严格字符串匹配补齐。
    transcript_lang = _detect_text_language(transcript_text)
    keywords_lang = _detect_text_language("".join(keywords))
    same_single_language = (
        transcript_lang == keywords_lang and transcript_lang in ["zh", "en"]
    )

    if any(not f for f in touched_flags) and (not same_single_language) and os.getenv("LLM_API_KEY"):
        to_expand = [keywords[i] for i, f in enumerate(touched_flags) if not f]
        alias_map = expand_keyword_aliases(to_expand)

        aliases_per_keyword: list[list[str]] = []
        for kw in keywords:
            aliases_per_keyword.append(alias_map.get(kw, []))

        touched_result = score_touched(
            transcript_text,
            keywords,
            aliases_per_keyword=aliases_per_keyword,
        )
        touched_flags = touched_result["touched_flags"][:]

    mentioned_result = score_mentioned(
        transcript_text=transcript_text,
        keywords=keywords,
        touched_flags=touched_flags,
    )
    total_touched = sum(1 for f in touched_flags if f)
    elapsed_ms = int((time.time() - start_ts) * 1000)

    return {
        "total_touched": total_touched,
        "total_mentioned": mentioned_result["total_mentioned"],
        "touched_flags": touched_flags,
        "mentioned_flags": mentioned_result["mentioned_flags"],
        "mentioned_confidences": mentioned_result.get("mentioned_confidences", []),
        "warning": mentioned_result.get("warning", ""),
        "transcript_text": transcript_text,
        "elapsed_ms": elapsed_ms,
        "status": "success",
    }

