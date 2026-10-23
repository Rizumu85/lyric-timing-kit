# 环境与模型

## 仓库与配置

脚本都在仓库的 `tools/` 里，从该目录运行。位置信息只来自仓库根目录的 `lyric-kit.config.json`（或环境变量 `LYRIC_KIT_CONFIG` 指向的文件）；没有就从 `lyric-kit.config.example.json` 复制一份。

| 字段 | 含义 |
| --- | --- |
| `musicDir` | 用户的音乐目录，必填 |
| `dataDir` | 数据目录，默认仓库下的 `data/`。每首歌的文件夹、备份、缓存、模型都在这里 |
| `python` | 对齐环境的解释器，默认仓库下的 `.venv` |
| `proxy` | 访问视频站需要代理时填写 |
| `webBridge` | 操作用户浏览器的本地服务地址，可选 |
| `singerAliases` | 合唱歌词表里歌手标签的缩写，例如 `{"k": "kana"}`，可选 |

数据目录里是有版权的歌词和用户的音乐备份，不要提交到任何公开仓库。

## 两个 Python 环境

识别模型和对齐模型对依赖版本的要求冲突，所以分开装：

- `.venv`：人声分离、强制对齐、读音检查。`requirements.txt`。
- `.venv-asr`：听写核对。`requirements-asr.txt`。

对齐辅助代码来自 FA-Kara，克隆到 `vendor/FA-Kara`；中文听写用的 FireRedASR2S 克隆到 `vendor/FireRedASR2S`。两者都不随本仓库分发。

## 模型

| 用途 | 模型 | 许可证 | 备注 |
| --- | --- | --- | --- |
| 人声分离 | BS-RoFormer（`model_bs_roformer_ep_317_sdr_12.9755.ckpt`） | 权重许可证不明确 | 默认。每首歌只分离这一次，对齐和各路读法都用它分出来的人声；经 audio-separator 首次使用时下载到 `<modelDir>/sep-models`，约 640 MB；在显卡上用半精度跑 |
| 人声分离（备用） | Demucs htdemucs | MIT（代码） | BS-RoFormer 跑不起来时（没装、显存不够）自动改用它 |
| 日语强制对齐 | NextFire/mms-300m-ForcedAligner-karaoke-ja-Latn | CC BY-NC-SA 4.0 | **禁止商用** |
| 其他语言强制对齐 | torchaudio MMS_FA | CC BY-NC 4.0 | **禁止商用**；日语歌里的英语句也用它 |
| 第二个读音模型 | RomajiASR v1.0.0 | 代码 Apache-2.0，权重以其发布页为准 | 日语歌声的音素识别。从 https://github.com/Xiantaidu/RomajiASR 的发布页下载 `model_safetensors.zip`（约 350 MB），解压到 `<modelDir>/asr-models/RomajiASR-v1.0.0/`（里面是 `config.json`、`model.safetensors`、`phoneme_vocab.json`）。用 transformers 直接加载，不需要它的代码。没装时 `onepass.py` 只用对齐模型自己的读法，读音检查也退回单模型 |
| 日语、英语听写 | Qwen/Qwen3-ASR-1.7B | Apache-2.0 | |
| 中文听写 | FireRedASR2-AED | Apache-2.0 | |

模型由使用者自己下载，本仓库不分发权重。因为两个对齐模型禁止商用，这套流程只能用于个人或非商业用途，向用户说明这一点。

显存：对齐 8 GB 足够；听写模型约需 8–10 GB。两者不要同时跑。一首三四分钟的歌在 16 GB 显存的显卡上大约十几秒，其中人声分离约 11 秒，几个模型各读一遍不到 1 秒，比对和对齐零点几秒；所以要提速只有分离这一步值得动，给对齐模型换推理引擎没有用。显存被别的程序占满时分离会慢好几倍。

选这两个模型的依据（都在同一批歌上，用人手动放过的句首当标准量过）：通用的听写模型（带时间戳的 Whisper、Qwen3-ASR 加对齐器、MOSS-Music、CrisperWhisper）定句首的误差在 0.3–0.9 秒，不如歌词表自带的时间（约 0.1 秒）；逐帧输出的模型才够准。RomajiASR 和对齐模型在两者都听到的音上只差约 0.02 秒，各自能钉住七成左右的音，合起来八成多。把主唱和和声分开的分离模型试过，没有帮助。换模型前先用 `onepass.py --eval-hand` 这样的办法量，不要凭印象。

## 音频格式

解码一律经 ffmpeg，所以 ffmpeg 能读的格式都能打轴。写入按文件里的标签种类处理，不看扩展名：

| 标签种类 | 格式 | 普通歌词 | 带注音的歌词 |
| --- | --- | --- | --- |
| ID3v2 | MP3、AAC、TTA、WAV、AIFF、DSF、DFF | 默认的 `USLT` 帧 | 描述为 `TimeTag-Ruby` 的 `USLT` 帧 |
| Vorbis 注释 | FLAC、OGG、Opus | `LYRICS` | `RUBY_LYRICS` |
| MP4 | M4A、ALAC | `©lyr` | `----:com.apple.iTunes:RUBY_LYRICS` |
| APEv2 | APE、WavPack | `Lyrics` | `RUBY_LYRICS` |
| ASF | WMA | `WM/Lyrics` | `RUBY_LYRICS` |

MP3 由 `batch_embed.mjs` 直接写，其余由 `embed_tags.py` 写。CAF 没有可用的歌词标签，会被拒绝。Monkey's Audio（APE）和 WavPack 共用代码，但只有 WavPack 实测过。

## 可选能力

- **视频下载**：从卡拉 OK 视频读取读音或时间时需要 yt-dlp；Bilibili 需要 yutto 和用户自己的登录。
- **用户浏览器**：部分歌词站拦截脚本请求，只能通过用户自己的浏览器读取。宿主没有这个能力时跳过对应步骤，不影响其余流程。
- **播放器里的“送去复查”按钮**：可选的 Folia 插件，由 `install_folia_mod.mjs` 安装，见 [试听与反馈](review.md)。
- **播放器**：内嵌歌词的 Ruby 部分使用 TimeTag/NicoKara 的 `@Ruby` 扩展。普通播放器只会读到普通双语歌词，这是预期行为。要显示注音，可以用 Folia 加「双语 · 注音歌词」模组（Folia 模组市场里有，源码在 https://github.com/Rizumu85/folia-bilingual-ruby-lyrics ）。
