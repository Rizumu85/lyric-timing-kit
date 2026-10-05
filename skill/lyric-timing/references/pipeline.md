# 流程与命令

命令都在 `tools/` 目录下运行。`<key>` 是一首歌的标识（音乐文件路径的哈希前 12 位），也是它数据文件夹的名字。带 `--keys-file` 的脚本接受一个每行一个 key 的文件。

## 每首歌的文件

| 文件 | 内容 | 谁写 |
| --- | --- | --- |
| `source.json` | 歌词表：每行原文、译文、原始时间。读音写在汉字后面的括号里 | 导入；之后只由人或 Agent 明确修改 |
| `prepared.json` | 上一次打轴时每行实际使用的读音 | 打轴 |
| `aligned.fia` | 草稿：逐字时间和注音 | 打轴、单行修改 |
| `result.json` | 状态。`complete` 表示已写入，`confirmed` 表示人确认过 | 各阶段 |
| `timing-edits.json` | 用户在试听页上的手动调整，写入时合并进草稿 | 试听页 |
| `line-notes.json` | 用户写给 Agent 的逐行备注 | 试听页 |
| `heard.json`、`lyric-check.json` | 听写结果，以及它和歌词的差异 | 版本核对 |
| `reading-check.json`、`reading-outliers.json` | 两种声学读音检查的结果 | 读音核对 |

`source.json` 是唯一的事实来源。要改歌词或读音就改它，再让对应的行重新对齐；不要直接改草稿里的文字。

### 读音的写法

- 汉字后面加括号：`運命(さだめ)`。括号里可以是平假名、片假名或罗马音；片假名会原样显示。
- 被注音的部分不全是汉字时，用 `｜` 标出起点：`｜夜の手(ナイトハンド)`。

## 阶段命令

### 1. 清点与导入

```text
python batch_inventory.py                # 首次：清点音乐目录里的 MP3，取出内嵌歌词
python add_songs.py                      # 登记还没登记的文件（各种格式；首次清点后也跑一遍，把非 MP3 的补上）
python set_lyrics.py <key> <歌词文件> [--write]   # 文件没有内嵌歌词、或内嵌的是错的时，换上一份歌词表
node dropped_text_scan.mjs               # 导入检查：被误删的行、掉落的词
node mixed_translation_scan.mjs          # 导入检查：粘在原文后面的译文
node clean_credits.mjs [--write]         # 制作信息、歌手名单
python fix_simplified_kanji.py [--write] # 日语歌词里混入的简体字
node find_translation.mjs <key> && node apply_translation.mjs <key>   # 缺译文时从音乐平台补
```

#### 译文缺行时去哪找

平台上的译文经常只缺几行：一行歌词被拆成两半时后半句没有译文，英文句和数拍子的句子没有译文。`find_translation.mjs` 只查音乐平台，查不到时按这个顺序找，不要直接自己翻：

1. **音乐平台上同一首歌的其他版本**：原唱、翻唱、现场版的译文往往出自同一份。
2. **视频站上带中文字幕的上传**：搜“歌名 + 中文字幕”或“歌名 + CC字幕”，优先选标题写明 CC 字幕的。CC 字幕是文本，只取字幕、不必下载视频（例如 `yutto --subtitle-only <视频地址>`，得到带时间的 `.srt`），几秒钟就能拿到整首的原文和译文对照。字幕烧在画面里的视频要抽帧再读，成本高得多，排在后面。
3. **都没有才自己翻译**，并告诉用户哪些行是自己补的。

采用前先拿已有的几行对比措辞：一致说明是同一位译者，直接补缺行；不一致就不要把两种译文混在一首歌里。字幕里原样保留的外语句（如 `Sit down`）保持不译。字幕按整句写、歌词表按半句拆时，把译文拆到对应的半句上。

### 2. 打轴

```text
python batch_align.py                    # 全部未打轴的歌
python realign_song.py <key>             # 按 source.json 重新打一首
python realign_song.py --anchor-embedded <key>   # 文件里已有歌词时，以其中的句首时间为锚
node realign_keys.mjs <keys file>        # 批量重排，自动为每首选择上面两种方式
node pin_to_source.mjs <key> <行号> ...  # 对不出来的行（拖长的哼唱、打码的词）放回歌词表的时间
```

重新对齐已经写入过的歌时用锚定方式。按歌词表原始时间开窗的方式会让整句提前约一秒，只在歌词表时间可信而整曲对齐明显失败时使用（`--windowed`）。

### 3. 歌词版本核对

```text
python transcribe_song.py <key>          # ASR 环境；中文歌用 transcribe_zh.py
python lyric_check.py <key>
```

`unheard` 是歌词里有但没听到的行，`extra` 是听到但歌词里没有的内容。英语和快节奏段落的误报多，判断前看具体内容。

### 4. 读音核对

见 [读音核对](readings.md)。

### 5–6. 试听与反馈

```text
node review_server.mjs                   # 试听页，默认 http://127.0.0.1:3417
node fix_lines.mjs <edits.json>          # 单行修改：改词、改读音、删行
```

`fix_lines.mjs` 的输入：

```json
{ "<key>": { "replace": [[行号, "原文片段", "新片段"]], "remove": [行号], "removeFrom": 行号 } }
```

它只重新对齐被改的行，并保持该行原来的句首时间；用户的手动调整留在各自的行上。

### 7. 写入

```text
node embed_confirmed.mjs <key> ...           # 人确认过的歌
node rerun_gate.mjs                          # 自动重排后，判断哪些歌可以不经试听写回
node embed_confirmed.mjs --rerun <key> ...   # 写回通过上一步的歌；不会碰人确认过的歌
```

各种格式的写法见 [环境与模型](setup.md) 的“音频格式”。第一次写某种格式时，先写一首并回读确认，再写其余的。写完告诉用户：播放器里要重新扫描或重新导入这些文件，否则显示的还是它缓存的旧歌词。

自动写回的条件由 `rerun_gate.mjs` 判定：行能一一对应、没有任何一行比文件里现有的时间移动超过 3 秒、移动超过 1 秒的行不超过一成。不满足的留给人听。

## 歌名

```text
node title_translations.mjs              # 查音乐平台上的通用译名
node review_server.mjs  → /titles        # 用户逐首选择是否加译名
python apply_title_choices.py [--write]  # 改文件名和标题标签，并迁移数据文件夹
python retitle.py "<文件名片段>" "<新译名>"
```

改文件名会改变 key，所以必须用这两个脚本，不要手动重命名。
