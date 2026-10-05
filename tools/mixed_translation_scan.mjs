// Find Japanese lyric rows where a Chinese translation was left glued to the end of the original
// (original has kana, no separate translation, and the last space-separated part looks Chinese).
// Writes <data>/译文混入原文.md.   Usage: node mixed_translation_scan.mjs
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { BATCH } from './kit-config.mjs';

// Characters that exist in simplified Chinese but not in Japanese writing.
const CHINESE_ONLY = /[这们个为来时说对没过还会让给从样里后经发现见长问间实当动爱开关东车话语爷妈吗呢吧啊么谁请谢难听写读买卖员图书馆进远运连选边钟铁银闻养饭饮马鱼鸟龙齐华词译论试课调谈误识议护贵资质赶赵转轻较载辆辈达迟适递逻遗邮释针钢钱错锁镇门闭闲队际陆险随隐难静韩顺须顾顿预领频题颜额风飞饿馆驾验骑鲜麦黄齿龄]/;
const out = ['# 疑似把中文译文留在原文末尾的歌词行', ''];
let songs = 0, rows = 0;
for (const key of fs.readdirSync(BATCH)) {
  const file = path.join(BATCH, key, 'source.json');
  if (!fs.existsSync(file) || key.endsWith('-rf')) continue;
  const source = JSON.parse(fs.readFileSync(file, 'utf8'));
  if (source.language !== 'ja' || !Array.isArray(source.rows)) continue;
  const hits = source.rows.map((row, index) => ({ row, index })).filter(({ row }) => {
    if (row.translation || !/[ぁ-ヿ]/.test(row.original)) return false;
    const parts = row.original.trim().split(/[\s　]+/), last = parts.at(-1);
    // Either it has characters Japanese does not use, or the song is otherwise translated and this tail is pure kanji.
    const translated = source.rows.filter(r => r.translation).length > source.rows.length * .5;
    return parts.length > 1 && last.length >= 4 && !/[ぁ-ヿ]/.test(last) && (CHINESE_ONLY.test(last) || (translated && /^[㐀-鿿，。！？、]+$/.test(last)));
  });
  if (!hits.length) continue;
  songs++; rows += hits.length;
  out.push(`## ${source.name}`, ...hits.map(({ row, index }) => `- 第 ${index + 1} 行：${row.original}`), '');
}
fs.writeFileSync(path.join(BATCH, '译文混入原文.md'), out.join('\n'), 'utf8');
console.log(`${songs} songs, ${rows} rows`);
