# 第三方代码与模型

本仓库不分发下列任何内容；这里只说明流程依赖什么、各自的许可证是什么。

## 模型

| 用途 | 模型 | 许可证 |
| --- | --- | --- |
| 人声分离 | Demucs htdemucs（`adefossez/HTDemucs`） | 代码 MIT |
| 人声分离（可选） | BS-RoFormer（经 audio-separator 下载） | 权重许可证未明确说明，自行判断 |
| 日语强制对齐 | `NextFire/mms-300m-ForcedAligner-karaoke-ja-Latn` | CC BY-NC-SA 4.0 |
| 其他语言强制对齐 | torchaudio `MMS_FA`（基于 `facebook/mms-300m`） | CC BY-NC 4.0 |
| 日语、英语听写 | `Qwen/Qwen3-ASR-1.7B` | Apache-2.0 |
| 中文听写 | `FireRedTeam/FireRedASR2-AED` | Apache-2.0 |

## 代码

| 项目 | 用途 | 许可证 |
| --- | --- | --- |
| [FA-Kara](https://github.com/moriwx/FA-Kara) | 歌词分词与对齐辅助（`haruraw2norm`、`align_yohane`），克隆到 `vendor/FA-Kara` | MIT |
| [FireRedASR2S](https://github.com/FireRedTeam/FireRedASR2S) | 中文听写，克隆到 `vendor/FireRedASR2S` | Apache-2.0 |
| pykakasi | 词典读音的后备 | GPL-3.0-or-later |
| mutagen | 读写各种格式的标签（标题、歌词） | GPL-2.0-or-later |
| janome | 日语分词与读音 | Apache-2.0 |
| demucs、audio-separator | 人声分离 | MIT |
| uroman | 韩语等文字的罗马化（对齐用） | Apache-2.0 |
| romkan、opencc、pypinyin、jaconv | 文字转换 | BSD / Apache-2.0 / MIT |
| torch、torchaudio、transformers、librosa | 推理与音频 | BSD / Apache-2.0 / ISC |
| yt-dlp、yutto（可选） | 下载卡拉 OK 视频用于核对读音 | Unlicense / GPL-3.0 |

`tools/format/` 与配套的 Folia 歌词模组中的同名文件同源，由同一作者编写。
