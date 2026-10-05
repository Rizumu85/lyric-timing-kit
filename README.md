# lyric-timing-kit

给本地音乐文件（MP3、FLAC、M4A、OGG、WAV 等）自动打轴：逐字时间轴、双语歌词、汉字注音（Ruby），写回音乐文件。附带一个让人“只看必须看的地方”的试听复查页面，以及把整套流程交给 AI Agent 执行的 Skill。

> Word-level lyric timing for local music files (MP3, FLAC, M4A, OGG, WAV and more; Japanese with furigana, Chinese, English, Korean with romanization), a review page built to minimise human checking, and an agent skill that runs the whole workflow. Documentation is in Chinese.

## 它做什么

1. **打轴**：人声分离 → 强制对齐，得到逐字时间。
2. **自检**：听写核对歌词版本；读音按“来源 → 上下文 → 录音”三层核对。
3. **复查**：本地试听页只标出可疑的歌和可疑的行，人可以微调时间、写备注、点“通过”。
4. **写入**：把普通双语歌词和带注音的逐字歌词写进音乐文件的标签（按格式用 ID3、Vorbis 注释、MP4、APEv2 或 ASF），写前备份，写后回读校验。

设计上最花心思的不是对齐，而是第 2、3 步：自动结果里哪些能信、哪些必须让人听。详见 `skill/lyric-timing/references/`。

## 开始使用

```text
git clone <本仓库>
cd lyric-timing-kit
cp lyric-kit.config.example.json lyric-kit.config.json    # 填 musicDir
python -m venv .venv                                      # 再按 requirements.txt 的注释装 torch 和依赖
git clone https://github.com/moriwx/FA-Kara vendor/FA-Kara
```

之后要么把 `skill/lyric-timing` 交给你的 Agent 让它按流程执行，要么自己照 `skill/lyric-timing/references/pipeline.md` 里的命令运行。试听页：双击 `listen.cmd`（或 `./listen.sh`）。

需要 Node 20+、Python 3.10+、ffmpeg 和一块 8 GB 以上显存的 NVIDIA 显卡。在 Windows 上完整验证过。

## 目录

| 路径 | 内容 |
| --- | --- |
| `tools/` | 全部脚本、试听页和歌名页的模板 |
| `tools/format/` | 歌词格式的读写（ID3、LRC、Ruby LRC），和配套的播放器模组保持一致 |
| `skill/lyric-timing/` | Agent Skill：流程、判断原则、命令和踩过的坑 |
| `folia-mod/lyrics-review/` | 可选的 Folia 播放器插件：听歌时一键把当前歌曲送回试听页 |
| `data/` | 你的数据（不进仓库）：每首歌的歌词表、草稿、备份、缓存、模型 |

## 播放器

带注音的逐字歌词使用 TimeTag/NicoKara 的 `@Ruby` 扩展，存放在一个单独的 ID3 歌词帧里；默认歌词帧是普通双语 LRC，任何播放器都能读。要显示注音，需要支持该扩展的播放器，例如装了 [双语 · 注音歌词](https://github.com/Rizumu85/folia-bilingual-ruby-lyrics) 模组的 Folia。

## 许可证与使用限制

本仓库的代码以 **GPL-3.0-or-later** 发布，见 `LICENSE`。

**只能用于个人或非商业用途**：流程使用的两个对齐模型分别以 CC BY-NC-SA 4.0 和 CC BY-NC 4.0 发布，禁止商用。模型和第三方代码都不随本仓库分发，由使用者自行获取，清单见 `THIRD-PARTY.md`。

歌词和音乐有版权。本仓库不包含任何歌词，你的 `data/` 目录也不应该公开。
