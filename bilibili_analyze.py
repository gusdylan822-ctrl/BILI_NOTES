#!/usr/bin/env python3
"""B站视频 AI 分析 · 第三步
用法:
    python3 bilibili_analyze.py BV14c4x6QEfz.txt
    python3 bilibili_analyze.py BV14c4x6QEfz            # 自动找 BV14c4x6QEfz.txt 或 BV14c4x6QEfz_asr.txt
    python3 bilibili_analyze.py BV14c4x6QEfz.txt --provider qwen
    python3 bilibili_analyze.py BV14c4x6QEfz.txt --provider openai --model gpt-4o-mini

支持的服务商（都是 OpenAI 兼容接口，--provider 切换，默认 deepseek）：
    deepseek  DeepSeek    https://platform.deepseek.com        环境变量 DEEPSEEK_API_KEY
    openai    OpenAI      https://platform.openai.com          环境变量 OPENAI_API_KEY
    moonshot  Kimi        https://platform.moonshot.cn         环境变量 MOONSHOT_API_KEY
    zhipu     智谱 GLM    https://open.bigmodel.cn             环境变量 ZHIPU_API_KEY
    qwen      通义千问    https://bailian.console.aliyun.com   环境变量 QWEN_API_KEY
    也可以用 --api-key 直接传 Key，或设 LLM_API_BASE 指向自定义兼容地址（比如本地 Ollama）。

流程: 读文字稿 -> （太长则分段摘要再汇总）-> 调所选服务商的 API -> 输出 Markdown 分析报告
费用以各家控制台为准；DeepSeek 一个视频分析大约 1 万 Token 输入 + 几千 Token 输出，也就几分钱。
    新账号通常有免费额度（以控制台实际显示为准）。
    API Key 相当于你的账号密码，不要发给别人、不要传到 GitHub 上。
"""
import argparse
import os
import re
import sys
from pathlib import Path

import requests

# 各家都是 OpenAI 兼容的 /chat/completions 接口，换地址 + 模型 + Key 就能用。
# label: 界面显示名；models: 该服务商的常用模型（第一个是默认）；env: Key 的环境变量名。
PROVIDERS = {
    "deepseek": {
        "label": "DeepSeek",
        "api_base": "https://api.deepseek.com",
        "models": ["deepseek-flash", "deepseek-v4-pro"],
        "env": "DEEPSEEK_API_KEY",
        "console": "https://platform.deepseek.com",
    },
    "openai": {
        "label": "OpenAI",
        "api_base": "https://api.openai.com/v1",
        "models": ["gpt-4o-mini", "gpt-4o"],
        "env": "OPENAI_API_KEY",
        "console": "https://platform.openai.com",
    },
    "moonshot": {
        "label": "Kimi（Moonshot）",
        "api_base": "https://api.moonshot.cn/v1",
        "models": ["kimi-k2", "moonshot-v1-8k"],
        "env": "MOONSHOT_API_KEY",
        "console": "https://platform.moonshot.cn",
    },
    "zhipu": {
        "label": "智谱 GLM",
        "api_base": "https://open.bigmodel.cn/api/paas/v4",
        "models": ["glm-4.5-flash", "glm-4-plus"],
        "env": "ZHIPU_API_KEY",
        "console": "https://open.bigmodel.cn",
    },
    "qwen": {
        "label": "通义千问",
        "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen-plus", "qwen-turbo"],
        "env": "QWEN_API_KEY",
        "console": "https://bailian.console.aliyun.com",
    },
}
DEFAULT_PROVIDER = "deepseek"
# 单次调用最多放这么多字，超过就先分段摘要再汇总
CHUNK_CHARS = 12000


def provider_api_url(provider: str) -> str:
    """该服务商的对话接口地址；LLM_API_BASE 环境变量可覆盖（用于自定义兼容地址，如本地 Ollama）。"""
    base = os.environ.get("LLM_API_BASE", "").strip() or PROVIDERS[provider]["api_base"]
    return base.rstrip("/") + "/chat/completions"

SYSTEM_ANALYZE = """你是一位视频内容分析助手。用户给你一段 B 站视频的文字稿，每行开头是 [时:分:秒] 时间戳。
请用中文输出一份 Markdown 分析报告，结构如下：

# 视频分析

## 全文内容总结
把这个视频的全部内容详细地总结出来：按视频本身的逻辑分成几个部分，每部分展开讲清楚讲了什么、怎么讲的、结论是什么。不要只用一两句话带过，也不要按时间戳逐条列要点。

## 客观性分析
- 创作者的说法里，哪些是事实陈述，哪些是个人观点、评价或推测，分开说。
- 有没有片面、夸大、情绪化煽动、只讲一面、选择性呈现的地方，有就具体指出，引用原话并带时间戳。
- 证据是否充分：有没有给出数据、来源或引用；只凭一张嘴说的关键结论，标出来。

## 准确性分析
- 有没有明显的事实错误、过时信息、可疑的数字、日期或人名，有就具体指出，并给出你认为正确的说法。
- 拿不准、没法从文字稿里验证的，写“无法核实”，不要硬编、不要假装确定。
- 文字稿里明显听错、语义不通的地方（语音转文字导致的），也列出来提醒核对。

## 能学到的东西
- 这个视频里真正有用的知识、方法或思路，分别列出来。
- 每个说清楚它为什么有用、能用在什么地方；可以马上用的，标上“可马上用”。

## 一句话总结
这个视频值不值得看，适合什么人看。

规则：只根据文字稿写，不要编造稿子里没有的内容；全文内容总结要详细展开，其他部分语言简洁，不要啰嗦。"""

SYSTEM_CHUNK = """你是一位视频内容分析助手。用户给你一段 B 站视频文字稿的其中一段，每行开头是 [时:分:秒] 时间戳。
请把它浓缩成两部分：
1. 内容要点：这段讲了什么，写得具体一点，保留关键细节、数据和例子，不要只剩标题式的短句，每条格式为“- [时间] 内容”。
2. 值得注意的说法：这段里可能片面、夸大、情绪化或可疑的说法，有就列出来，格式为“- [时间] 原话/说法：为什么值得注意”，没有就写“无”。"""

SYSTEM_SYNTHESIZE = """你是一位视频内容分析助手。下面是同一个 B 站视频各段的要点汇总（每段都带时间戳，也包括各段里值得注意的说法）。
请把它们整合成一份完整的 Markdown 分析报告，结构如下：

# 视频分析

## 全文内容总结
把这个视频的全部内容详细地总结出来：按视频本身的逻辑分成几个部分，每部分展开讲清楚讲了什么、怎么讲的、结论是什么。不要只用一两句话带过，也不要按时间戳逐条列要点。

## 客观性分析
- 根据汇总里标出的值得注意的说法，分析创作者的讲解哪些是事实陈述，哪些是个人观点、评价或推测。
- 有没有片面、夸大、情绪化煽动、只讲一面、选择性呈现的地方，有就具体指出，引用原话并带时间戳。
- 证据是否充分：有没有给出数据、来源或引用；只凭一张嘴说的关键结论，标出来。

## 准确性分析
- 有没有明显的事实错误、过时信息、可疑的数字、日期或人名，有就具体指出，并给出你认为正确的说法。
- 拿不准、没法从汇总里验证的，写“无法核实”，不要硬编、不要假装确定。
- 汇总里明显听错、语义不通的地方，也列出来提醒核对。

## 能学到的东西
- 这个视频里真正有用的知识、方法或思路，分别列出来。
- 每个说清楚它为什么有用、能用在什么地方；可以马上用的，标上“可马上用”。

## 一句话总结
这个视频值不值得看，适合什么人看。

规则：只根据给出的要点写，不要编造没有的内容；全文内容总结要详细展开，其他部分语言简洁，不要啰嗦。"""


def resolve_input(arg: str) -> Path:
    p = Path(arg)
    if p.is_file():
        return p
    m = re.search(r"BV[0-9A-Za-z]{10}", arg)
    if m:
        bvid = m.group(0)
        for cand in (Path(f"{bvid}.txt"), Path(f"{bvid}_asr.txt")):
            if cand.is_file():
                return cand
        raise FileNotFoundError(
            f"没找到 {bvid}.txt 或 {bvid}_asr.txt，"
            "请先跑第一步（字幕提取）或第二步（音频转文字）")
    raise FileNotFoundError(f"找不到文件: {arg}")


def split_chunks(text: str, max_chars: int = CHUNK_CHARS) -> list:
    """按行切块，每块不超过 max_chars 字，不断行。"""
    chunks, cur = [], ""
    for line in text.splitlines(keepends=True):
        if cur and len(cur) + len(line) > max_chars:
            chunks.append(cur)
            cur = ""
        cur += line
    if cur:
        chunks.append(cur)
    return chunks


def call_llm(provider: str, api_key: str, messages: list, model: str):
    """调一次所选服务商的对话接口，返回 (正文, 用量字典)。"""
    cfg = PROVIDERS[provider]
    r = requests.post(
        provider_api_url(provider),
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
        json={"model": model, "messages": messages,
              "temperature": 0.3, "stream": False},
        timeout=180,
    )
    if r.status_code == 401:
        raise RuntimeError(f"API Key 无效，请检查 {cfg['env']} 是否正确（{cfg['label']}）")
    if r.status_code == 402:
        raise RuntimeError(f"余额不足：请去 {cfg['console']} 充值，或先看账号里有没有免费额度")
    if r.status_code == 429:
        raise RuntimeError("请求太频繁（429），稍等一会儿再试")
    if r.status_code != 200:
        raise RuntimeError(f"{cfg['label']} 返回错误 {r.status_code}: {r.text[:300]}")
    data = r.json()
    return (data["choices"][0]["message"]["content"],
            data.get("usage", {}))


def analyze(transcript: str, api_key: str, model: str, provider: str = DEFAULT_PROVIDER):
    """分析文字稿，返回 (报告 Markdown, 总用量字典)。"""
    total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    if len(transcript) <= CHUNK_CHARS:
        print("文字稿不长，一次分析完。")
        content, usage = call_llm(provider, api_key, [
            {"role": "system", "content": SYSTEM_ANALYZE},
            {"role": "user", "content": transcript},
        ], model)
        _add_usage(total_usage, usage)
        return content, total_usage
    chunks = split_chunks(transcript)
    print(f"文字稿较长（{len(transcript)} 字），分成 {len(chunks)} 段，先逐段摘要再汇总。")
    summaries = []
    for i, chunk in enumerate(chunks, 1):
        print(f"正在摘要第 {i}/{len(chunks)} 段……")
        s, usage = call_llm(provider, api_key, [
            {"role": "system", "content": SYSTEM_CHUNK},
            {"role": "user", "content": chunk},
        ], model)
        _add_usage(total_usage, usage)
        summaries.append(s)
    print("正在汇总成完整报告……")
    content, usage = call_llm(provider, api_key, [
        {"role": "system", "content": SYSTEM_SYNTHESIZE},
        {"role": "user", "content": "\n\n".join(summaries)},
    ], model)
    _add_usage(total_usage, usage)
    return content, total_usage


def _add_usage(total: dict, usage: dict) -> None:
    for k in total:
        total[k] += usage.get(k, 0)


def main() -> None:
    ap = argparse.ArgumentParser(description="B站视频 AI 分析")
    ap.add_argument("input", help="文字稿 .txt 文件，或 BV 号")
    ap.add_argument("--provider", default=DEFAULT_PROVIDER, choices=list(PROVIDERS),
                    help=f"AI 服务商，默认 {DEFAULT_PROVIDER}（可选：{', '.join(PROVIDERS)}）")
    ap.add_argument("--model", default=None,
                    help="模型，不填用该服务商的默认模型")
    ap.add_argument("--api-key", default=None, help="API Key（也可设环境变量）")
    ap.add_argument("--api-base", default=None,
                    help="自定义兼容接口地址，如本地 Ollama：http://localhost:11434/v1")
    args = ap.parse_args()
    cfg = PROVIDERS[args.provider]
    if args.api_base:
        os.environ["LLM_API_BASE"] = args.api_base
    model = args.model or cfg["models"][0]
    api_key = (args.api_key or os.environ.get(cfg["env"], "")).strip()
    if not api_key:
        print(f"没找到 {cfg['env']}，请先设置 {cfg['label']} 的 API Key：")
        print(f"  1. 打开 {cfg['console']} 注册登录，创建一个 API key")
        print(f"  2. 终端里跑: export {cfg['env']}=你的key")
        print(f"  或运行时加上 --api-key 你的key")
        sys.exit(1)
    src = resolve_input(args.input)
    transcript = src.read_text(encoding="utf-8")
    if not transcript.strip():
        print(f"{src} 是空的，先确认文字稿已生成。")
        sys.exit(1)
    print(f"正在分析: {src}（{len(transcript)} 字，{cfg['label']} 模型 {model}）")
    report, usage = analyze(transcript, api_key, model, args.provider)
    out = src.with_name(src.stem + "_analysis.md")
    out.write_text(report, encoding="utf-8")
    print(f"已保存: {out}")
    print(f"用量: 输入 {usage['prompt_tokens']} Token / "
          f"输出 {usage['completion_tokens']} Token / "
          f"共 {usage['total_tokens']} Token")


if __name__ == "__main__":
    main()
