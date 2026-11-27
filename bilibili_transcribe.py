#!/usr/bin/env python3
"""B站视频音频转文字 · 第二步
用法:
    python3 bilibili_transcribe.py BV14c4x6QEfz [--model base] [--keep-audio]

流程: yt-dlp 下载音频(mp3) -> faster-whisper 转文字 -> 输出带时间戳的 .txt 和 .srt
适用: 没有内置字幕的视频（第一步拿不到字幕时走这条路）。

需要:
    pip install -r requirements.txt   # yt-dlp, faster-whisper, requests
    ffmpeg（系统装一下，macOS: brew install ffmpeg）
模型第一次运行时会自动从 Hugging Face 下载（base 约 140MB）。
建议用虚拟环境，避免和系统 Python 冲突：
    python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

import requests


def parse_bvid(text: str) -> str:
    """从 BV 号或视频链接里提取 BV 号，支持 b23.tv 短链接。"""
    m = re.search(r"BV[0-9A-Za-z]{10}", text)
    if m:
        return m.group(0)
    # App 里分享出来的 b23.tv 短链接本身没有 BV 号，跟随跳转拿到真实链接再解析
    short = re.search(r"https?://b23\.tv/\S+", text)
    if short:
        try:
            r = requests.get(short.group(0),
                             headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com/"},
                             timeout=15)
            m2 = re.search(r"BV[0-9A-Za-z]{10}", r.url)
            if m2:
                return m2.group(0)
        except Exception:
            pass
        raise ValueError("这个 b23.tv 短链接打不开或跳转后没找到 BV 号，"
                         "请在浏览器里打开它，复制地址栏里的完整链接再试")
    raise ValueError("没找到 BV 号，请输入 BV 号或 B 站视频链接")


def download_audio(bvid: str) -> Path:
    url = f"https://www.bilibili.com/video/{bvid}"
    out = Path(f"{bvid}.mp3")
    if out.exists():
        print(f"音频已存在，跳过下载: {out}")
        return out
    print(f"正在下载音频: {url}")
    # 用当前 Python 解释器跑 yt-dlp 模块，不依赖 PATH 里有没有 yt-dlp 命令
    cmd = [sys.executable, "-m", "yt_dlp", "-x", "--audio-format", "mp3",
           "-o", str(out), "--no-playlist", url]
    r = subprocess.run(cmd)
    if r.returncode != 0 or not out.exists():
        raise RuntimeError("音频下载失败，请检查 BV 号、网络，或该视频是否需要登录")
    print(f"音频已保存: {out}")
    return out


def decode_audio_ffmpeg(audio: Path):
    """用 ffmpeg 把音频解码成 16kHz 单声道 float32 数组。
    不走 faster-whisper 自带的 av 解码，避开 av 各版本和 Python 新版本的兼容问题。"""
    import numpy as np
    cmd = ["ffmpeg", "-v", "error", "-i", str(audio),
           "-f", "f32le", "-ac", "1", "-ar", "16000", "-"]
    r = subprocess.run(cmd, stdout=subprocess.PIPE)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg 解码音频失败，请确认已安装 ffmpeg")
    return np.frombuffer(r.stdout, dtype=np.float32)


def transcribe(audio: Path, model_name: str) -> list:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("没装 faster-whisper，先跑: pip install faster-whisper")
        sys.exit(1)
    print(f"正在转文字（模型 {model_name}，第一次运行会先下载模型）……")
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    audio_array = decode_audio_ffmpeg(audio)
    # language=None 让 Whisper 自动识别主要语种（中文、英文或中英混杂的视频都能转；
    # 之前写死 language="zh" 会把英文语音硬转成中文）。混杂语音以主要语种为准，
    # 少数句子可能认错，字幕路径优先用 B 站原字幕更准。
    segments, _info = model.transcribe(
        audio_array, language=None, beam_size=5, vad_filter=True)
    return [(s.start, s.end, s.text.strip()) for s in segments if s.text.strip()]


def fmt_ts(seconds: float) -> str:
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:05.2f}"


def format_outputs(segments: list) -> tuple[str, str]:
    """把识别出的段落拼成 txt 和 srt 两种文本。"""
    lines = [f"[{fmt_ts(start)}] {text}" for start, end, text in segments]
    txt = "\n".join(lines)
    srt = []
    for i, (start, end, text) in enumerate(segments, 1):
        srt.append(str(i))
        srt.append(f"{fmt_ts(start).replace('.', ',')} --> {fmt_ts(end).replace('.', ',')}")
        srt.append(text)
        srt.append("")
    return txt, "\n".join(srt)


def transcribe_to_text(bvid: str, model: str = "base", keep_audio: bool = False) -> tuple[str, str]:
    """下载音频并转文字（给软件界面用）。返回 (txt文本, srt文本)。"""
    audio = download_audio(bvid)
    try:
        segments = transcribe(audio, model)
    finally:
        if not keep_audio:
            audio.unlink(missing_ok=True)
    if not segments:
        raise RuntimeError("没有识别到语音内容。")
    return format_outputs(segments)


def main() -> None:
    ap = argparse.ArgumentParser(description="B站视频音频转文字")
    ap.add_argument("video", help="BV 号或视频链接")
    ap.add_argument("--model", default="base",
                    choices=["tiny", "base", "small", "medium"],
                    help="Whisper 模型，默认 base（越大越准越慢）")
    ap.add_argument("--keep-audio", action="store_true", help="保留下载的 mp3")
    args = ap.parse_args()
    bvid = parse_bvid(args.video)
    try:
        txt, srt = transcribe_to_text(bvid, args.model, args.keep_audio)
    except RuntimeError as e:
        print(e)
        sys.exit(0)
    Path(f"{bvid}_asr.txt").write_text(txt, encoding="utf-8")
    Path(f"{bvid}_asr.srt").write_text(srt, encoding="utf-8")
    print(f"共 {len(txt.splitlines())} 段，已保存: {bvid}_asr.txt / {bvid}_asr.srt")
    print("--- 前 5 段预览 ---")
    print("\n".join(txt.splitlines()[:5]))
    if not args.keep_audio:
        print(f"已删除临时音频 {bvid}.mp3（加 --keep-audio 可保留）")


if __name__ == "__main__":
    main()
