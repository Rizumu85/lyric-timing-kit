// Find lyric text that was lost when a song's embedded lyric sheet was imported:
//  A. a first line that shared its timestamp with the staff credits and was discarded with them;
//  B. words that fell out when a "original translation" line was split into its two halves.
// Compares each song's original-lyrics.txt with its source.json. Writes 漏词排查.md, changes nothing.
// Usage: node dropped_text_scan.mjs
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { BATCH } from './kit-config.mjs';

const readJson = file => { try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch { return null; } };
const squash = text => (text || '').normalize('NFKC').replace(/[(（][ぁ-ヿa-zA-Z]+[)）]/g, '').replace(/[\s　]+/g, '');
const CREDIT = /(作词|作詞|作曲|编曲|編曲|制作人|混音|母带|和声|吉他|贝斯|鼓|录音|监制|出品|演唱|原唱|翻唱|词|曲|Vocal|Mix|Lyrics?|Music|Arrange\w*|Composer|Producer)\s*[:：]\s*\S+/gi;
const out = ['# 导入时可能漏掉的歌词', ''];
let songs = 0;
for (const key of fs.readdirSync(BATCH)) {
  const source = readJson(path.join(BATCH, key, 'source.json'));
  const rawFile = path.join(BATCH, key, 'original-lyrics.txt');
  if (!source?.rows || !fs.existsSync(rawFile) || key.endsWith('-rf')) continue;
  const result = readJson(path.join(BATCH, key, 'result.json')) || {};
  const found = [];
  // A: text left in a discarded credit line once the credits themselves are taken out
  for (const credit of source.discardedCredits || []) {
    const rest = (credit.text || '').replace(CREDIT, ' ').trim();
    const kana = rest.match(/[ぁ-ヿ]/g) || [];
    if (kana.length >= 3 && !source.rows.some(row => squash(row.original).includes(squash(rest.split(/\s+/)[0])) && Math.abs(row.sourceStart - credit.sourceStart) < 5)) found.push(`A  ${credit.sourceStart}s 被当成制作信息删掉的行里还有：${rest}`);
  }
  // B: raw line minus the row's original and translation leaves something
  const raw = fs.readFileSync(rawFile, 'utf8').split(/\r?\n/).map(line => /^\[(\d+):(\d+(?:\.\d+)?)\](.*)$/.exec(line)).filter(Boolean).map(m => [Number(m[1]) * 60 + Number(m[2]), m[3]]);
  source.rows.forEach((row, index) => {
    const line = raw.find(([time]) => Math.abs(time - row.sourceStart) < 0.02);
    if (!line) return;
    let rest = squash(line[1]);
    for (const part of [squash(row.original), squash(row.translation)]) if (part && rest.includes(part)) rest = rest.replace(part, '');
    // only when both halves were found is what remains a real leftover
    if (rest.length >= 2 && rest.length <= 12 && /[ぁ-ヿ㐀-鿿]/.test(rest) && squash(line[1]).includes(squash(row.original))) found.push(`B  第 ${index + 1} 行「${row.original}」原始行里多出：${rest}`);
  });
  if (!found.length) continue;
  songs++;
  out.push(`## ${source.name}${result.confirmed ? '（已确认写入）' : ''}`, `key ${key}`, '', ...found.map(item => '- ' + item), '');
  console.log(`${result.confirmed ? 'confirmed ' : '          '}${key} ${source.name}: ${found.length}`);
}
out.splice(1, 0, '', `${songs} 首歌有疑似漏掉的内容。`);
fs.writeFileSync(path.join(BATCH, '漏词排查.md'), out.join('\n'), 'utf8');
console.log(songs, 'songs flagged');
