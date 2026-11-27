# bilibili-video-analyzer

提取哔哩哔哩视频的字幕，接入 AI 做分析。最终目标是一个小软件：输入视频链接，输出摘要、要点、问答。

## 现在做到哪一步

**第一步：字幕提取（已完成）** —— `bilibili_subtitle.py`

输入 BV 号或视频链接，输出带时间戳的字幕文本（`.txt`）和一份 `.srt`。

**第二步：音频转文字（已完成）** —— `bilibili_transcribe.py`

没有内置字幕的视频用这条路：yt-dlp 下载音频，faster-whisper（Whisper 的加速版）转文字，输出带时间戳的 `<BV号>_asr.txt` 和 `<BV号>_asr.srt`。转文字时用 ffmpeg 直接解码音频，不走 faster-whisper 自带的 av 解码，避开 av 各版本和 Python 新版本（比如 3.14）的兼容问题。

```bash
python3 bilibili_transcribe.py BV14c4x6QEfz
python3 bilibili_transcribe.py BV14c4x6QEfz --model small --keep-audio
```

模型默认 `base`（中文够用，笔记本能跑）；`--model` 可选 tiny / base / small / medium，越大越准越慢。模型第一次运行时会自动从 Hugging Face 下载（base 约 140MB）。

**第三步：AI 分析（已完成）** —— `bilibili_analyze.py`

把第一步或第二步出的文字稿交给 AI 分析，输出 Markdown 报告（`_analysis.md`）：一句话摘要、带时间戳的视频要点、时间轴章节、金句，以及听错提醒。

支持 5 家服务商（都是 OpenAI 兼容接口，`--provider` 切换，默认 DeepSeek）：

| 服务商 | `--provider` | 拿 Key 的地方 | 环境变量 |
|---|---|---|---|
| DeepSeek | `deepseek` | https://platform.deepseek.com | `DEEPSEEK_API_KEY` |
| OpenAI | `openai` | https://platform.openai.com | `OPENAI_API_KEY` |
| Kimi | `moonshot` | https://platform.moonshot.cn | `MOONSHOT_API_KEY` |
| 智谱 GLM | `zhipu` | https://open.bigmodel.cn | `ZHIPU_API_KEY` |
| 通义千问 | `qwen` | https://bailian.console.aliyun.com | `QWEN_API_KEY` |

```bash
export DEEPSEEK_API_KEY=你的key
python3 bilibili_analyze.py BV14c4x6QEfz.txt
python3 bilibili_analyze.py BV14c4x6QEfz            # 自动找 .txt 或 _asr.txt
python3 bilibili_analyze.py BV14c4x6QEfz.txt --provider qwen
python3 bilibili_analyze.py BV14c4x6QEfz.txt --provider openai --model gpt-4o-mini
python3 bilibili_analyze.py BV14c4x6QEfz.txt --provider qwen --api-key 你的key
```

文字稿太长时会自动分段摘要再汇总。不填 `--model` 就用该服务商的默认模型（DeepSeek 默认 `deepseek-flash`，便宜够用）。想接本地模型也可以：`--api-base http://localhost:11434/v1`（Ollama 的兼容地址）。

拿 Key 的办法（以 DeepSeek 为例）：打开 https://platform.deepseek.com 注册登录 → 左侧 API keys → 创建 API key，复制 `sk-` 开头的值（只显示一次，马上保存）。新账号通常有 500 万 Token 免费额度（30 天有效，以控制台实际显示为准）；一个视频分析大约 1 万 Token 输入 + 几千 Token 输出，也就几分钱。Key 相当于账号密码，不要发给别人、不要传到 GitHub 上。

## 用法

```bash
pip install -r requirements.txt
python3 bilibili_subtitle.py BV14c4x6QEfz
python3 bilibili_subtitle.py https://www.bilibili.com/video/BV14c4x6QEfz
python3 bilibili_subtitle.py BV14c4x6QEfz --lang en    # 只要英文字幕
python3 bilibili_subtitle.py BV14c4x6QEfz --lang both  # 中英双语都提取（中文存 BV号.txt，英文存 BV号_en.txt）
```

## 登录（取字幕需要）

B 站现在要求登录才能取字幕（实测：不带登录 Cookie 时字幕列表为空，yt-dlp 也会提示 `Subtitles are only available when logged in`）。所以跑脚本前要先设置 `BILI_SESSDATA`：

1. 在浏览器里登录 B 站
2. 打开开发者工具（F12）→ Application → Cookies → `https://www.bilibili.com`
3. 找到 `SESSDATA`，复制它的值
4. 再运行：

```bash
BILI_SESSDATA=<你的值> python3 bilibili_subtitle.py BV14c4x6QEfz
```

注意：SESSDATA 相当于你的登录凭证，不要发给别人、不要传到 GitHub 上。脚本只在本地用，不会上传。

没有 SESSDATA 时脚本会明确提示。**下一步（音频 + 语音转文字）不需要登录**，公开视频直接能下音频。

输出示例：

```
视频: GitHub十一月第一周热榜：光盘能画画，手机能替你干活 (aid=..., cid=...)
字幕: 中文（自动生成） (ai-zh)
共 312 条字幕，已保存: BV14c4x6QEfz.txt / BV14c4x6QEfz.srt
--- 前 5 条预览 ---
[00:00:01.20] 大家好，欢迎来到……
```

没有内置字幕的视频会明确提示，下一步处理。

## 原理（一步就三件事）

1. `api.bilibili.com/x/web-interface/view?bvid=BV号` → 拿到 `aid` 和 `cid`
2. `api.bilibili.com/x/player/v2?aid=..&cid=..` → 拿到字幕列表（`subtitle.subtitles`，每条有 `subtitle_url`）
3. 下载字幕 JSON，`body` 数组里每条是 `{from, to, content}`，按时间排好输出

## 下一步

- **第四步：做成软件（已完成）** —— `app.py`，Gradio 界面

```bash
python3 app.py
```

浏览器会自动打开 http://127.0.0.1:7860。输入 BV 号或链接，填 SESSDATA（取字幕用，可不填），选处理模式和字幕语言，选 AI 服务商并填对应 Key，点“开始处理”：先取字幕，没有字幕就自动转文字；“提取字幕 + AI 分析”模式再调所选服务商分析，报告直接显示在页面上，文字稿和报告都能下载。

- **AI 服务商**：DeepSeek / OpenAI / Kimi（Moonshot）/ 智谱 GLM / 通义千问，选谁就填谁家的 Key；换服务商时模型列表会跟着变。
- **忽略字幕，直接语音转文字**：B 站偶尔会给一条视频返回串了视频的 AI 字幕（取到的文字稿和视频完全对不上），勾上这个就跳过字幕，直接用 Whisper 转写；同时程序会检查文字稿覆盖的时长，不到视频一半时在状态栏警告。
- **处理模式**：“仅提取字幕”只出文字稿，不用填 API Key、不花 Token；“提取字幕 + AI 分析”才是完整流程。
- **字幕语言**：中文 / English / 中英双语。B 站的字幕列表里每条轨道都带语言（`lan` 和 `lan_doc`，比如 `ai-zh` 中文、`ai-en` 英文），所以中英都有时能分开识别；选“中英双语”会把两份都取下来（中文存 `BV号.txt`，英文存 `BV号_en.txt`），分析用中文稿。要的语言没有时会明确提示（单语言模式会退回已有的语言并说明）。
- 没有字幕的视频走语音转文字：Whisper 自动识别主要语种，中英混杂也能转（以主要语种为准，少数句子可能认错）。

想停掉就回终端按 Ctrl+C。

## 桌面快捷方式

`启动B站视频分析.command`：把它放到桌面，双击就能启动（会打开一个终端窗口，里面跑着服务器；按 Ctrl+C 或关掉终端就停了）。
第一次用之前，在终端里跑一次 `chmod +x ~/Desktop/启动B站视频分析.command` 给它执行权限；如果 macOS 弹 Gatekeeper 提示，右键点它 → 打开。

## 安装建议：用虚拟环境

直接往系统 Python 里装包有时会冲突（比如系统自带的包卸不掉）。推荐每个项目单独建一个虚拟环境：

```bash
python3 -m venv .venv
source .venv/bin/activate   # macOS / Linux；Windows 用 .venv\Scripts\activate
pip install -r requirements.txt
```

另外第二步还需要系统装一个 `ffmpeg`（yt-dlp 靠它转 mp3），macOS：`brew install ffmpeg`。
