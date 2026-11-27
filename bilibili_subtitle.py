#!/usr/bin/env python3
"""B站视频字幕提取 · 第一步
用法:
    python3 bilibili_subtitle.py BV14c4x6QEfz
    python3 bilibili_subtitle.py https://www.bilibili.com/video/BV14c4x6QEfz
    python3 bilibili_subtitle.py BV14c4x6QEfz --lang en    # 只要英文字幕
    python3 bilibili_subtitle.py BV14c4x6QEfz --lang both  # 中英双语都提取

输出: 同目录下生成 <BV号>.txt（带时间戳的纯文本）和 <BV号>.srt
只用 requests。B 站现在要求登录才能取字幕，所以需要提供 SESSDATA：
在浏览器里登录 B 站，开发者工具 → Application → Cookies → 复制 SESSDATA 的值，
然后 BILI_SESSDATA=<你的值> python3 bilibili_subtitle.py BV号
没有 SESSDATA 时会明确提示（下一步做音频转文字，不需要登录）。
"""
import argparse
import os
import re
import sys
from pathlib import Path

import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Referer": "https://www.bilibili.com/",
}

VIEW_API = "https://api.bilibili.com/x/web-interface/view"
PLAYER_API = "https://api.bilibili.com/x/player/v2"
FINGER_API = "https://api.bilibili.com/x/frontend/finger/spi"  # 拿 buvid3（设备标识），过 B 站风控

# 优先选的中文语言代码（B站字幕 lan 字段）
PREFER_LANGS = ("zh-CN", "ai-zh", "zh-Hans", "zh-Hant")
ZH_LANGS = PREFER_LANGS
# 英文字幕语言代码（B站字幕 lan 字段）
EN_LANGS = ("en", "ai-en")


def parse_bvid(text: str) -> str:
    """从 BV 号或视频链接里提取 BV 号，支持 b23.tv 短链接。"""
    m = re.search(r"BV[0-9A-Za-z]{10}", text)
    if m:
        return m.group(0)
    # App 里分享出来的 b23.tv 短链接本身没有 BV 号，跟随跳转拿到真实链接再解析
    short = re.search(r"https?://b23\.tv/\S+", text)
    if short:
        try:
            r = requests.get(short.group(0), headers=HEADERS, timeout=15)
            m2 = re.search(r"BV[0-9A-Za-z]{10}", r.url)
            if m2:
                return m2.group(0)
        except Exception:
            pass
        raise ValueError("这个 b23.tv 短链接打不开或跳转后没找到 BV 号，"
                         "请在浏览器里打开它，复制地址栏里的完整链接再试")
    raise ValueError("没找到 BV 号，请输入 BV 号或 B 站视频链接")


def get_buvid3() -> str | None:
    """从 finger/spi 接口拿一个 buvid3（设备标识）。
    B 站的风控会拦下没有 buvid3 的请求（412），带上它就能正常访问。"""
    try:
        r = requests.get(FINGER_API, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return r.json()["data"]["b_3"]
    except Exception:
        return None


def get_video_info(bvid: str, headers: dict | None = None) -> dict:
    r = requests.get(VIEW_API, params={"bvid": bvid},
                     headers=headers or HEADERS, timeout=15)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取视频信息失败: {data.get('message')}")
    return data["data"]


def get_subtitle_list(aid: int, cid: int, headers: dict | None = None) -> list:
    r = requests.get(PLAYER_API, params={"aid": aid, "cid": cid},
                     headers=headers or HEADERS, timeout=15)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取字幕列表失败: {data.get('message')}")
    return data["data"].get("subtitle", {}).get("subtitles", []) or []


def pick_subtitle(subtitles: list, lang: str = "zh") -> dict | None:
    """按语言偏好挑一条字幕轨道。
    lang="zh": 优先中文；lang="en": 优先英文。
    偏好的语言没有时退回第一条（调用方用返回的 lan/lan_doc 判断实际拿到了哪种语言）。"""
    if not subtitles:
        return None
    prefer = EN_LANGS if lang == "en" else ZH_LANGS
    for code in prefer:
        for s in subtitles:
            if s.get("lan") == code:
                return s
    return subtitles[0]


def fetch_subtitle_body(url: str, headers: dict | None = None) -> list:
    if url.startswith("//"):
        url = "https:" + url
    r = requests.get(url, headers=headers or HEADERS, timeout=15)
    r.raise_for_status()
    return r.json().get("body", []) or []


def get_video_title(bvid: str, sessdata: str = "") -> str | None:
    """只取视频标题（转文字路径用，让界面显示到底取的是哪个视频）。"""
    return get_video_meta(bvid, sessdata).get("title")


def get_video_meta(bvid: str, sessdata: str = "") -> dict:
    """取视频标题和总时长（秒）。出错返回 {}。"""
    try:
        buvid3 = get_buvid3()
        headers = dict(HEADERS)
        cookies = []
        if buvid3:
            cookies.append(f"buvid3={buvid3}")
        sessdata = (sessdata or os.environ.get("BILI_SESSDATA", "")).strip()
        if sessdata:
            cookies.append(f"SESSDATA={sessdata}")
        if cookies:
            headers["Cookie"] = "; ".join(cookies)
        info = get_video_info(bvid, headers)
        return {"title": info.get("title"), "duration": info.get("duration")}
    except Exception:
        return {}


def check_coverage(last_ts: float, duration) -> str:
    """字幕覆盖检查：字幕最后一条时间戳应该接近视频总时长。
    覆盖不到一半时返回警告文案，说明这份字幕可能属于别的视频；否则返回空串。"""
    if not duration or not last_ts:
        return ""
    if last_ts < duration * 0.5:
        return (f"⚠️ 注意：这份文字稿只到 {fmt_ts(last_ts)}，"
                f"视频共 {fmt_ts(duration)}，覆盖不到一半，"
                "内容可能不完整或属于别的视频，请先核对文字稿再分析。")
    return ""


def get_transcript(bvid: str, sessdata: str = "", lang: str = "zh") -> dict | None:
    """取字幕全文（给软件界面用）。
    返回 {"txt": 带时间戳纯文本, "srt": srt 文本, "title": 标题,
           "aid": aid, "cid": cid, "lan_doc": 字幕语言名, "lan": 字幕语言代码,
           "count": 条数}，没有字幕返回 None。sessdata 不传时用环境变量 BILI_SESSDATA。
    lang="zh" 优先中文、lang="en" 优先英文；偏好的语言没有时退回第一条字幕。"""
    buvid3 = get_buvid3()
    headers = dict(HEADERS)
    cookies = []
    if buvid3:
        cookies.append(f"buvid3={buvid3}")
    sessdata = (sessdata or os.environ.get("BILI_SESSDATA", "")).strip()
    if sessdata:
        cookies.append(f"SESSDATA={sessdata}")
    if cookies:
        headers["Cookie"] = "; ".join(cookies)
    info = get_video_info(bvid, headers)
    aid, cid = info["aid"], info["cid"]
    subtitles = get_subtitle_list(aid, cid, headers)
    if not subtitles:
        return None
    sub = pick_subtitle(subtitles, lang)
    body = fetch_subtitle_body(sub["subtitle_url"], headers)
    lines = [f"[{fmt_ts(item['from'])}] {item['content']}" for item in body]
    txt = "\n".join(lines)
    srt_lines = []
    for i, item in enumerate(body, 1):
        srt_lines.append(str(i))
        srt_lines.append(
            f"{fmt_ts(item['from']).replace('.', ',')} --> "
            f"{fmt_ts(item['to']).replace('.', ',')}")
        srt_lines.append(item["content"])
        srt_lines.append("")
    return {"txt": txt, "srt": "\n".join(srt_lines), "title": info["title"],
            "aid": aid, "cid": cid, "lan_doc": sub.get("lan_doc"),
            "lan": sub.get("lan"), "count": len(body),
            # 下面两项用来校验字幕和视频对不对得上：字幕最后一条的时间戳
            # 应该接近视频总时长，差太多就说明这份字幕可能属于别的视频。
            "duration": info.get("duration"),
            "last_ts": max((item.get("to", 0) for item in body), default=0)}


def get_transcript_strict(bvid: str, sessdata: str = "", lang: str = "zh") -> dict | None:
    """和 get_transcript 一样，但偏好的语言不存在时返回 None（不退回其他语言）。
    中英双语都提取时用这个，避免把同一条中文轨道当成英文再存一份。"""
    result = get_transcript(bvid, sessdata, lang)
    if result is None:
        return None
    want = EN_LANGS if lang == "en" else ZH_LANGS
    return result if result.get("lan") in want else None


def fmt_ts(seconds: float) -> str:
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:05.2f}"


def main() -> None:
    ap = argparse.ArgumentParser(description="B站视频字幕提取")
    ap.add_argument("video", help="BV 号或视频链接")
    ap.add_argument("--lang", default="zh", choices=["zh", "en", "both"],
                    help="字幕语言：zh 中文（默认）、en 英文、both 中英双语都提取")
    args = ap.parse_args()
    bvid = parse_bvid(args.video)
    try:
        if args.lang == "both":
            results = []
            for lang, stem in (("zh", bvid), ("en", f"{bvid}_en")):
                result = get_transcript_strict(bvid, lang=lang)
                if result is None:
                    print(f"没有找到{'中文' if lang == 'zh' else '英文'}字幕。")
                    continue
                Path(f"{stem}.txt").write_text(result["txt"], encoding="utf-8")
                Path(f"{stem}.srt").write_text(result["srt"], encoding="utf-8")
                print(f"字幕: {result['lan_doc']} ({result['lan']})，"
                      f"共 {result['count']} 条，已保存: {stem}.txt / {stem}.srt")
                results.append(result)
            if not results:
                print("这个视频没有返回字幕。")
                _print_login_hint()
            sys.exit(0)
        result = get_transcript(bvid, lang=args.lang)
    except requests.exceptions.RequestException as e:
        print(f"网络请求失败，请检查网络后重试：{e}")
        sys.exit(1)
    if result is None:
        print("这个视频没有返回字幕。")
        _print_login_hint()
        sys.exit(0)
    print(f"视频: {result['title']} (aid={result['aid']}, cid={result['cid']})")
    print(f"字幕: {result['lan_doc']} ({result['lan']})")
    warn = check_coverage(result.get("last_ts", 0), result.get("duration"))
    if warn:
        print(warn)
    out_txt = Path(f"{bvid}.txt")
    out_txt.write_text(result["txt"], encoding="utf-8")
    # 同时写一份 .srt，方便导入剪辑软件或播放器
    Path(f"{bvid}.srt").write_text(result["srt"], encoding="utf-8")
    print(f"共 {result['count']} 条字幕，已保存: {out_txt} / {bvid}.srt")
    print("--- 前 5 条预览 ---")
    print("\n".join(result["txt"].splitlines()[:5]))


def _print_login_hint() -> None:
    if not os.environ.get("BILI_SESSDATA", "").strip():
        print("提示：B 站现在要求登录才能取字幕。请设置环境变量 BILI_SESSDATA")
        print("（浏览器登录 B 站后，开发者工具 → Application → Cookies → 复制 SESSDATA 的值），再重新运行。")
        print("不想用登录的话也没关系，下一步（音频 + 语音转文字）不需要登录也能做。")


if __name__ == "__main__":
    main()
