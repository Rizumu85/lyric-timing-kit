# lyric-timing

## 有什么用

让 Agent 给本地歌曲做逐字歌词时间轴、双语歌词和汉字注音，并把结果写回音乐文件。

自动打轴的结果里总有错：读音标错、歌词版本和录音对不上、整句偏移。不用这个 Skill 时，要么全靠人逐首逐句听，要么带着错写进文件。这个 Skill 把一套验证过的流程交给 Agent：机器能确认的由机器确认，确认不了的缩小成几行，再用一个试听页交给人。

## 安装

本 Skill 是 lyric-timing-kit 仓库的一部分，脚本都在同一个仓库里，所以需要整个仓库：

1. 把仓库地址 `https://github.com/Rizumu85/lyric-timing-kit` 交给你的 Agent，让它克隆仓库并安装其中的 `skill/lyric-timing`。
2. 或者运行 `npx skills add Rizumu85/lyric-timing-kit`，再自己克隆仓库以获得脚本。

`npx` 只是安装工具，不是运行依赖。

## 配置

首次使用时，Agent 会按 `references/setup.md` 检查并引导完成：

- 复制 `lyric-kit.config.example.json` 为 `lyric-kit.config.json`，填入音乐目录。
- 建两个 Python 环境，下载模型，克隆 FA-Kara。

配置文件和数据目录都不进仓库。不要把音乐、歌词或本机路径提交上去。

## 使用

用自然语言说就行，例如：

- “给我音乐文件夹里的歌打轴。”
- “这首歌的歌词时间不对，帮我重新对一下。”
- “我在试听页上标完了，处理一下。”
- “检查一下这几首日语歌的假名读音。”

## 兼容性与依赖

- 在 Windows 11 + NVIDIA 显卡上完整验证过。脚本不依赖 Windows，其他系统未验证。
- 需要 Node 20+、Python 3.10+、ffmpeg、8 GB 以上显存。
- 读取被网站拦截的歌词页时，需要宿主能操作用户的浏览器；没有这个能力时该步骤跳过，其余流程不受影响。
- 显示注音需要支持 TimeTag `@Ruby` 扩展的播放器；普通播放器只显示普通双语歌词。

## 数据与适用边界

- 音频和歌词只在本机处理。查找读音来源、译名和译文时会访问歌词站、音乐平台和视频站；模型从 Hugging Face 下载。
- 会修改音乐文件的 ID3 歌词帧，每次修改前都会备份原文件。
- 不产生费用。两个对齐模型禁止商用，因此只能用于个人或非商业用途。
- 不适用：只翻译歌词、下载歌曲、给视频做字幕、没有本地音频文件。

## 输出

- 音乐文件里的两份内嵌歌词：普通双语 LRC 和带逐字时间与注音的 Ruby LRC。
- 数据目录里每首歌一个文件夹，保存歌词表、草稿、检查结果和备份记录。
- 每轮结束时 Agent 给出一份清单：写入了哪些、改了什么、哪些是推断、哪些需要重听。

## 测试

```text
python <skill 校验工具>/validate_skill.py skill/lyric-timing --public --strict --weak-model --universal
node --test tools/tests/format.test.mjs
```

脚本一运行就会处理全部歌曲，所以不要用“导入一下”或“空参数运行”来测试它们；检查语法用 `node --check` 和 Python 的语法检查。
