"""
上传音频预处理：统一转为 16kHz / mono / s16 WAV，供 ASR 切片流程使用。

- .wav：直接落盘（内部 ASR 仍会按片重采样，与现有一致）
- .mp3 / .m4a：依赖本机已安装 ffmpeg 并可在 PATH 中调用
- .pcm：按「原始 s16le、16000Hz、单声道」解码（见 README 说明）
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

# 裸 PCM 默认参数（与讯飞 raw 常见约定一致；若不符需先自行转 WAV）
PCM_SAMPLE_RATE = 16000
PCM_FORMAT = "s16le"
PCM_CHANNELS = 1


def _ffmpeg_path() -> str | None:
    # 1) 允许用户显式指定（最高优先级）
    env_path = os.getenv("FFMPEG_PATH", "").strip()
    if env_path and os.path.isfile(env_path):
        return env_path

    # 2) 尝试从 PATH 获取
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg

    # 3) 兜底：从 winget 安装目录中定位（避免 PATH 不生效导致找不到）
    local_app_data = os.getenv("LOCALAPPDATA", "")
    if not local_app_data:
        return None
    packages_dir = os.path.join(local_app_data, "Microsoft", "WinGet", "Packages")
    if not os.path.isdir(packages_dir):
        return None

    try:
        for pkg_name in os.listdir(packages_dir):
            if not pkg_name.lower().startswith("gyan.ffmpeg_"):
                continue
            pkg_dir = os.path.join(packages_dir, pkg_name)
            if not os.path.isdir(pkg_dir):
                continue

            # 典型结构：Gyan.FFmpeg_.../ffmpeg-*-full_build/bin/ffmpeg.exe
            for child in os.listdir(pkg_dir):
                if not child.lower().startswith("ffmpeg-"):
                    continue
                cand = os.path.join(pkg_dir, child, "bin", "ffmpeg.exe")
                if os.path.isfile(cand):
                    return cand
    except Exception:
        # 定位失败直接返回 None，由上层给出清晰报错
        return None

    return None


def write_upload_to_wav(audio_bytes: bytes, filename: str | None) -> tuple[str, list[str]]:
    """
    将上传字节写入临时文件，必要时经 ffmpeg 转为 WAV。

    Returns:
        (wav_path, cleanup_paths)  # 分析结束后应删除 cleanup_paths 中全部文件
    """
    name = filename or "upload.wav"
    ext = os.path.splitext(name)[1].lower() or ".wav"
    cleanup: list[str] = []

    if ext == ".wav":
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.write(audio_bytes)
        tmp.close()
        cleanup.append(tmp.name)
        return tmp.name, cleanup

    ffmpeg = _ffmpeg_path()
    if not ffmpeg:
        raise RuntimeError(
            "上传了非 WAV 格式，需要本机安装 ffmpeg 并完成转码。"
            "当前自动定位失败：请安装 ffmpeg，或将 ffmpeg 加入 PATH；"
            "也可以在环境变量设置 FFMPEG_PATH 指向 ffmpeg.exe。"
            "参考：https://ffmpeg.org/download.html"
        )

    src = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
    src.write(audio_bytes)
    src.close()
    cleanup.append(src.name)

    dst = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    dst.close()
    cleanup.append(dst.name)

    if ext == ".pcm":
        cmd = [
            ffmpeg,
            "-y",
            "-f",
            PCM_FORMAT,
            "-ar",
            str(PCM_SAMPLE_RATE),
            "-ac",
            str(PCM_CHANNELS),
            "-i",
            src.name,
            "-ar",
            "16000",
            "-ac",
            "1",
            "-sample_fmt",
            "s16",
            dst.name,
        ]
    else:
        cmd = [
            ffmpeg,
            "-y",
            "-i",
            src.name,
            "-ar",
            "16000",
            "-ac",
            "1",
            "-sample_fmt",
            "s16",
            dst.name,
        ]

    try:
        subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.CalledProcessError as e:
        err = (e.stderr or e.stdout or str(e)).strip()
        raise RuntimeError(f"ffmpeg 转码失败（{ext}）：{err}") from e
    except FileNotFoundError as e:
        raise RuntimeError("未找到 ffmpeg 可执行文件，请检查 PATH。") from e

    return dst.name, cleanup
