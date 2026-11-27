#!/usr/bin/env python3
"""B站视频分析 · 第四步：Gradio 软件界面
用法:
    python3 app.py
然后浏览器会自动打开 http://127.0.0.1:7860（如果没自动打开，手动复制这个地址到浏览器）。

流程：输入 BV 号或链接 -> 选处理模式（仅提取字幕 / 提取字幕 + AI 分析）
     和字幕语言（中文 / English / 中英双语），再选 AI 服务商
     （DeepSeek / OpenAI / Kimi / 智谱 GLM / 通义千问，都是 OpenAI 兼容接口）
     -> 先取字幕（需要 SESSDATA 登录，没有就跳过）
     -> 没有字幕就下载音频转文字 -> 分析模式再调所选服务商的 API 分析 -> 显示报告，可下载文字稿和报告。
     字幕不对时可以勾“忽略字幕，直接语音转文字”，跳过字幕直接用 Whisper 转写。
"""
from pathlib import Path
import re

import gradio as gr

from bilibili_subtitle import (EN_LANGS, ZH_LANGS, check_coverage,
                               get_transcript, get_transcript_strict,
                               get_video_meta, parse_bvid)
from bilibili_transcribe import transcribe_to_text
from bilibili_analyze import DEFAULT_PROVIDER, PROVIDERS, analyze

MODE_EXTRACT_ONLY = "仅提取字幕"
MODE_ANALYZE = "提取字幕 + AI 分析"
LANG_LABELS = {"中文": "zh", "English": "en", "中英双语": "both"}
# 界面显示的服务商名 -> bilibili_analyze.py 里的 key
PROVIDER_LABELS = {cfg["label"]: key for key, cfg in PROVIDERS.items()}
DEFAULT_LABEL = PROVIDERS[DEFAULT_PROVIDER]["label"]


def _last_ts_seconds(txt: str) -> float:
    """从带时间戳的文字稿里取最后一条的时间戳（秒）。转文字路径用。"""
    times = re.findall(r"\[(\d+):(\d+):([\d.]+)\]", txt or "")
    if not times:
        return 0.0
    h, m, s = times[-1]
    return int(h) * 3600 + int(m) * 60 + float(s)


def run_pipeline(bvid_input, sessdata, api_key, whisper_model, model,
                 mode, subtitle_lang, provider_label, force_asr=False):
    """Gradio 生成器：逐步返回 (状态, 报告, 文字稿文件, 报告文件)。"""
    # 1. 检查输入
    try:
        bvid = parse_bvid(bvid_input or "")
    except ValueError:
        yield "请输入正确的 BV 号或 B 站视频链接。", "", None, None
        return
    provider = PROVIDER_LABELS.get(provider_label, DEFAULT_PROVIDER)
    cfg = PROVIDERS[provider]
    analyze_mode = (mode == MODE_ANALYZE)
    if analyze_mode and not (api_key or "").strip():
        yield (f"需要 {cfg['label']} 的 API Key 才能分析：打开 {cfg['console']} "
               "注册登录，创建一个 API key 填进来。"), "", None, None
        return
    api_key = (api_key or "").strip()
    sessdata = (sessdata or "").strip()
    want = LANG_LABELS.get(subtitle_lang, "zh")

    # 2. 先取字幕（勾了“强制转文字”就跳过这步），没有就转文字
    transcripts = []  # 每项 {"txt","srt","stem","lan_doc","lan"}
    notes = []
    title = None
    if not force_asr:
        try:
            yield "正在取字幕……", "", None, None
            if want == "both":
                for lang, stem, name in (("zh", bvid, "中文"), ("en", bvid + "_en", "英文")):
                    result = get_transcript_strict(bvid, sessdata, lang)
                    if result is None:
                        notes.append(f"没有找到{name}字幕")
                    else:
                        transcripts.append({**result, "stem": stem})
                        title = title or result["title"]
            else:
                result = get_transcript(bvid, sessdata, want)
                if result is not None:
                    transcripts.append({**result, "stem": bvid})
                    title = result["title"]
                    prefer = EN_LANGS if want == "en" else ZH_LANGS
                    if result.get("lan") not in prefer:
                        notes.append(
                            f"没有找到{'英文' if want == 'en' else '中文'}字幕，"
                            f"已用{result.get('lan_doc') or '其他语言'}代替")
        except Exception as e:
            yield f"取字幕时出错：{e}\n请检查网络，或直接用第一步、第二步的命令行脚本。", "", None, None
            return
    if transcripts:
        source = "、".join(f"字幕（{t['lan_doc']}）" for t in transcripts)
    else:
        if force_asr:
            yield "已选择忽略字幕，正在下载音频转文字……", "", None, None
        else:
            yield "没有取到字幕（可能需要登录，或视频本身没有字幕），正在下载音频转文字……", "", None, None
        try:
            transcript, srt_text = transcribe_to_text(bvid, whisper_model)
        except Exception as e:
            yield f"转文字时出错：{e}", "", None, None
            return
        transcripts = [{"txt": transcript, "srt": srt_text, "stem": bvid + "_asr",
                        "lan_doc": "语音转文字", "lan": None}]
        source = "语音转文字"
        meta = get_video_meta(bvid, sessdata)
        title = meta.get("title") or bvid
        transcripts[0]["duration"] = meta.get("duration")
    txt_paths = []
    for t in transcripts:
        txt_path = Path(f"{t['stem']}.txt")
        srt_path = Path(f"{t['stem']}.srt")
        txt_path.write_text(t["txt"], encoding="utf-8")
        srt_path.write_text(t["srt"], encoding="utf-8")
        txt_paths.append(str(txt_path))
    if title is None:
        title = bvid
    files_out = txt_paths[0] if len(txt_paths) == 1 else txt_paths

    # 选分析用的那份稿（优先中文），并检查它和视频对不对得上：
    # 文字稿最后一条时间戳应该接近视频总时长，覆盖不到一半就警告。
    main_text = next((t for t in transcripts if t.get("lan") in ZH_LANGS),
                     transcripts[0])
    last_ts = main_text.get("last_ts")
    if last_ts is None:
        last_ts = _last_ts_seconds(main_text["txt"])
    cov_note = check_coverage(last_ts, main_text.get("duration"))

    # 3. 只提取字幕：不用 Key、不分析，直接返回文字稿
    if not analyze_mode:
        status = f"已提取字幕，未做 AI 分析。视频：{title}。文字稿来源：{source}。"
        if notes:
            status += "（" + "；".join(notes) + "）"
        if cov_note:
            status += "\n" + cov_note
        yield (status,
               "仅提取字幕模式：文字稿已保存，在下面下载。\n\n"
               "如需 AI 分析，把上面的模式改成“提取字幕 + AI 分析”再跑一次。",
               files_out, None)
        return

    # 4. AI 分析（优先用中文稿，没有中文就用已有的那份）
    yield f"已拿到文字稿（来源：{source}），视频：{title}，正在用 {cfg['label']} 分析……", "", None, None
    try:
        report, usage = analyze(main_text["txt"], api_key, model, provider)
    except Exception as e:
        yield (f"分析时出错：{e}\n文字稿已保存，可以先看看文字稿，"
               "修好 Key 或余额问题后再分析。"), "", files_out, None
        return
    if title and title != bvid:
        # 让报告标题里带上视频名，一眼就能看出这份报告是哪个视频的
        report = report.replace("# 视频分析", f"# 视频分析：{title}", 1)
    md_path = Path(f"{main_text['stem']}_analysis.md")
    md_path.write_text(report, encoding="utf-8")
    status = (f"完成！视频：{title}。文字稿来源：{source}。分析用的是{main_text['lan_doc']}稿。"
              f"用量：输入 {usage['prompt_tokens']} Token / "
              f"输出 {usage['completion_tokens']} Token。")
    if notes:
        status += "（" + "；".join(notes) + "）"
    if cov_note:
        status += "\n" + cov_note
    yield status, report, files_out, str(md_path)


with gr.Blocks(title="B站视频分析") as demo:
    gr.Markdown("# B站视频分析\n输入 BV 号或视频链接，自动取字幕（没有就转文字）。可以只提取字幕，也可以再用 AI 分析成报告。")
    with gr.Row():
        bvid_input = gr.Textbox(label="BV 号或视频链接", placeholder="BV14c4x6QEfz", scale=3)
        whisper_model = gr.Dropdown(label="转文字模型", choices=["base", "small"],
                                    value="base", scale=1)
    with gr.Row():
        mode = gr.Radio(label="处理模式", choices=[MODE_EXTRACT_ONLY, MODE_ANALYZE],
                        value=MODE_ANALYZE, scale=1)
        subtitle_lang = gr.Dropdown(label="字幕语言", choices=list(LANG_LABELS),
                                    value="中文", scale=1)
        force_asr_input = gr.Checkbox(label="忽略字幕，直接语音转文字（字幕不对时用）",
                                      value=False, scale=1)
    sessdata_input = gr.Textbox(label="BILI_SESSDATA（取字幕需要，没有可不填）",
                                type="password", placeholder="浏览器登录 B 站后从 Cookies 里复制")
    with gr.Row():
        provider_input = gr.Dropdown(label="AI 服务商", choices=list(PROVIDER_LABELS),
                                     value=DEFAULT_LABEL, scale=1)
        model_input = gr.Dropdown(label="分析模型",
                                  choices=PROVIDERS[DEFAULT_PROVIDER]["models"],
                                  value=PROVIDERS[DEFAULT_PROVIDER]["models"][0], scale=1)
    api_key_input = gr.Textbox(label="AI API Key（分析模式需要，仅提取字幕不用填）",
                               type="password", placeholder="在上面选的服务商官网创建一个填进来")

    def on_provider_change(label):
        key = PROVIDER_LABELS.get(label, DEFAULT_PROVIDER)
        models = PROVIDERS[key]["models"]
        return gr.update(choices=models, value=models[0])

    provider_input.change(on_provider_change, inputs=[provider_input],
                          outputs=[model_input])
    btn = gr.Button("开始处理", variant="primary")
    status = gr.Textbox(label="状态", interactive=False)
    gr.Markdown("## 分析报告")
    report = gr.Markdown()
    with gr.Row():
        transcript_file = gr.File(label="下载文字稿 (.txt)")
        report_file = gr.File(label="下载分析报告 (.md)")
    btn.click(run_pipeline,
              inputs=[bvid_input, sessdata_input, api_key_input,
                      whisper_model, model_input, mode, subtitle_lang,
                      provider_input, force_asr_input],
              outputs=[status, report, transcript_file, report_file])

if __name__ == "__main__":
    demo.launch(inbrowser=True)
