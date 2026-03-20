import os
import time
import json
import csv
from datetime import datetime, timezone
from io import StringIO

import streamlit as st
from dotenv import load_dotenv

from analyzer_service import analyze_audio_and_keywords
from keyword_parser import parse_keywords

try:
    from streamlit_mic_recorder import mic_recorder
except Exception:  # noqa: BLE001
    mic_recorder = None


def main() -> None:
    load_dotenv()
    st.set_page_config(page_title="企业AI工作流 Demo", page_icon="🎯", layout="centered")
    st.title("企业AI工作流 Demo")
    st.info("输入要求：语音时长不超过30分钟；关键词数量不超过100个；每个关键词不超过10个字。")
    if "recorded_audio_bytes" not in st.session_state:
        st.session_state["recorded_audio_bytes"] = None
    if "recorded_audio_name" not in st.session_state:
        st.session_state["recorded_audio_name"] = None

    input_mode = st.radio(
        "语音输入方式",
        options=["网页录音", "上传文件"],
        horizontal=True,
    )
    audio_file = None
    recorded_audio = None
    if input_mode == "网页录音":
        if hasattr(st, "audio_input"):
            recorded_audio = st.audio_input("点击录音并停止后提交（浏览器麦克风）")
            if recorded_audio is not None:
                st.session_state["recorded_audio_bytes"] = bytes(recorded_audio.getbuffer())
                st.session_state["recorded_audio_name"] = "recorded.wav"
        elif mic_recorder is not None:
            mic_data = mic_recorder(
                start_prompt="开始录音",
                stop_prompt="停止录音",
                just_once=False,
                use_container_width=True,
                key="mic_recorder_widget",
            )
            if mic_data and isinstance(mic_data, dict) and mic_data.get("bytes"):
                st.session_state["recorded_audio_bytes"] = bytes(mic_data["bytes"])
                # mic_recorder 通常返回 webm 字节，用 webm 后缀让后续转码路径更稳妥。
                st.session_state["recorded_audio_name"] = "recorded.webm"
                recorded_audio = st.session_state["recorded_audio_bytes"]
        else:
            st.warning("当前环境未启用网页录音组件，请先安装 streamlit-mic-recorder。")

        if st.session_state.get("recorded_audio_bytes"):
            st.success("已捕获录音，可直接点击“开始分析”。")
            st.audio(st.session_state["recorded_audio_bytes"])
    else:
        audio_file = st.file_uploader(
            "上传音频（WAV 直接识别；MP3/M4A/PCM 需本机已安装 ffmpeg 并加入 PATH）",
            type=["wav", "mp3", "m4a", "pcm"],
            key="file_uploader_widget",
        )

    keywords_text = st.text_area(
        "关键词输入（支持 ， , 、 / ; 换行 空格）",
        height=180,
        placeholder="示例：企业开户，增值税专票/社保，报税",
    )
    submitted = st.button("开始分析", type="primary")

    parsed_preview = parse_keywords(keywords_text)
    st.caption(f"已解析关键词数量：{len(parsed_preview)}")
    if parsed_preview:
        st.write("关键词预览（前20个）：", parsed_preview[:20])

    if submitted:
        input_bytes = None
        input_name = None
        if input_mode == "网页录音":
            recorded_bytes = st.session_state.get("recorded_audio_bytes")
            recorded_name = st.session_state.get("recorded_audio_name")
            if not recorded_bytes:
                st.error("请先完成网页录音。")
                return
            input_bytes = bytes(recorded_bytes)
            input_name = recorded_name or "recorded.webm"
        else:
            if audio_file is None:
                st.error("请先上传音频文件。")
                return
            input_bytes = audio_file.getbuffer()
            input_name = audio_file.name

        keywords = parse_keywords(keywords_text)
        if not keywords:
            st.error("请至少输入 1 个关键词。")
            return

        if len(keywords) > 100:
            st.error("关键词数量不能超过 100，超出后无法执行。")
            return
        long_keywords = [kw for kw in keywords if len(kw) > 10]
        if long_keywords:
            st.error(f"存在超过10个字的关键词（例如：{long_keywords[0]}），无法执行。")
            return

        st.success("输入校验通过，开始执行统一分析流程。")
        st.info(f"本次解析关键词数量：{len(keywords)}")
        progress = st.progress(0, text="准备开始")
        status_text = st.empty()

        begin = time.time()
        try:
            status_text.info("阶段 1/3：上传数据与预处理")
            progress.progress(15, text="阶段 1/3：上传数据与预处理")
            time.sleep(0.1)

            status_text.info("阶段 2/3：ASR 转写中")
            progress.progress(55, text="阶段 2/3：ASR 转写中")
            result = analyze_audio_and_keywords(
                input_bytes,
                keywords,
                upload_filename=input_name,
            )
            status_text.info("阶段 3/3：触达与语义评分中")
            progress.progress(85, text="阶段 3/3：触达与语义评分中")
            time.sleep(0.1)
            progress.progress(100, text="分析完成")
            status_text.success("流程执行完成")
        except Exception as exc:  # noqa: BLE001
            progress.empty()
            status_text.empty()
            msg = str(exc)
            if "ASR" in msg or "转写" in msg:
                st.error(f"ASR 阶段失败：{msg}")
            elif "ffmpeg" in msg.lower():
                st.error(f"音频预处理失败：{msg}")
            elif "LLM" in msg:
                st.error(f"语义提及阶段失败：{msg}")
            else:
                st.error(f"分析失败：{msg}")
            return

        analyzed_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        st.session_state["last_analysis"] = {
            "input_name": input_name,
            "keywords": keywords,
            "result": result,
            "analyzed_at": analyzed_at,
            "export_ts": int(time.time()),
        }

    last = st.session_state.get("last_analysis")
    if not last:
        return

    input_name = last["input_name"]
    keywords = last["keywords"]
    result = last["result"]
    analyzed_at = last["analyzed_at"]
    export_ts = last["export_ts"]

    total_touched = result["total_touched"]
    total_mentioned = result["total_mentioned"]
    touched_flags = result["touched_flags"]
    mentioned_flags = result["mentioned_flags"]
    mentioned_confidences = result.get("mentioned_confidences", [])
    transcript_text = result["transcript_text"]
    warning = result["warning"]
    elapsed_ms = result.get("elapsed_ms", 0)

    if warning:
        st.warning(warning)

    col1, col2 = st.columns(2)
    with col1:
        st.metric("精准触达总分", total_touched)
    with col2:
        st.metric("语义提及总分", total_mentioned)
    st.caption(f"本次总耗时：{elapsed_ms} ms")

    with st.expander("调试信息（T8）", expanded=False):
        st.write("文件名：", input_name)
        st.write("关键词列表（前20个）：", keywords[:20])
        st.write("转写文本（前200字）：", transcript_text[:200])
        st.write("触达布尔结果（前20个）：", touched_flags[:20])
        st.write("语义提及布尔结果（前20个）：", mentioned_flags[:20])

    detail_rows = []
    for i, kw in enumerate(keywords):
        detail_rows.append(
            {
                "keyword": kw,
                "touched": bool(touched_flags[i]),
                "mentioned": bool(mentioned_flags[i]),
                "mentioned_confidence": float(mentioned_confidences[i]) if mentioned_confidences and i < len(mentioned_confidences) else None,
            }
        )

    export_payload = {
        "analyzed_at": analyzed_at,
        "filename": input_name,
        "total_touched": total_touched,
        "total_mentioned": total_mentioned,
        "keyword_count": len(keywords),
        "details": detail_rows,
    }

    csv_buffer = StringIO(newline="")
    writer = csv.DictWriter(
        csv_buffer,
        fieldnames=[
            "timestamp",
            "filename",
            "keyword",
            "touched",
            "mentioned",
            "mentioned_confidence",
            "total_touched",
            "total_mentioned",
        ],
    )
    writer.writeheader()
    for row in detail_rows:
        writer.writerow(
            {
                "timestamp": analyzed_at,
                "filename": input_name,
                "keyword": row["keyword"],
                "touched": int(row["touched"]),
                "mentioned": int(row["mentioned"]),
                "mentioned_confidence": row.get("mentioned_confidence"),
                "total_touched": total_touched,
                "total_mentioned": total_mentioned,
            }
        )

    st.subheader("结果导出")
    dcol1, dcol2 = st.columns(2)
    with dcol1:
        st.download_button(
            label="下载 JSON",
            data=json.dumps(export_payload, ensure_ascii=False, indent=2),
            file_name=f"analysis_result_{export_ts}.json",
            mime="application/json",
        )
    with dcol2:
        st.download_button(
            label="下载 CSV",
            data=csv_buffer.getvalue().encode("utf-8-sig"),
            file_name=f"analysis_result_{export_ts}.csv",
            mime="text/csv",
        )


if __name__ == "__main__":
    main()
