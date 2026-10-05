// Put lines the aligner could not place (held "ah"s, censored words, ad-libs) back on the
// times of the lyric sheet: the line starts at its sourceStart, its words are spread evenly up
// to the next line (at most as long as the aligner made the line), and the line before it is
// cut short if it ran into this one. Changes only the draft (aligned.fia).
// Usage: node pin_to_source.mjs <key> <line number> [<line number> ...]      (1-based)
import fs from 'node:fs';
import path from 'node:path';
import { BATCH } from './kit-config.mjs';

const [key, ...numbers] = process.argv.slice(2);
const folder = path.join(BATCH, key);
const source = JSON.parse(fs.readFileSync(path.join(folder, 'source.json'), 'utf8'));
const file = path.join(folder, 'aligned.fia'), doc = JSON.parse(fs.readFileSync(file, 'utf8'));
const lines = doc.lyrics.lines, round = value => Math.round(value * 100) / 100;
if (lines.length !== source.rows.length) throw new Error('草稿和歌词表行数不一致');
// every timed node of a line, cut back so that nothing ends after `limit`
const clamp = (node, limit) => {
  for (const name of ['startTime', 'endTime']) if (typeof node[name] === 'number' && node[name] > limit) node[name] = limit;
  for (const name of ['words', 'syllables', 'ruby']) for (const child of node[name] || []) clamp(child, limit);
};
for (const number of numbers.map(Number)) {
  const line = lines[number - 1], start = source.rows[number - 1]?.sourceStart;
  if (!line || typeof start !== 'number') throw new Error(`第 ${number} 行没有歌词表时间`);
  if (number > 1 && lines[number - 2].startTime >= start) throw new Error(`第 ${number} 行的歌词表时间早于上一行的开始，不能这样放回`);
  const next = lines[number]?.startTime ?? line.endTime;
  const end = round(Math.max(start + .3, Math.min(next - .05, start + Math.max(1.5, line.endTime - line.startTime))));
  const words = line.words || [], step = (end - start) / Math.max(1, words.length);
  words.forEach((word, index) => {
    const from = round(start + step * index), to = round(start + step * (index + 1));
    const parts = word.syllables || [], inner = (to - from) / Math.max(1, parts.length);
    Object.assign(word, { startTime: from, endTime: to });
    parts.forEach((part, at) => {
      Object.assign(part, { startTime: round(from + inner * at), endTime: round(from + inner * (at + 1)) });
      const ruby = part.ruby || [], small = inner / Math.max(1, ruby.length);
      ruby.forEach((reading, n) => Object.assign(reading, { startTime: round(part.startTime + small * n), endTime: round(part.startTime + small * (n + 1)) }));
    });
  });
  console.log(`第 ${number} 行「${line.fullText}」 ${line.startTime.toFixed(2)} -> ${start.toFixed(2)}–${end.toFixed(2)}`);
  Object.assign(line, { startTime: round(start), endTime: end });
  if (number > 1 && lines[number - 2].endTime > start - .05) clamp(lines[number - 2], round(start - .05));
}
fs.writeFileSync(file, JSON.stringify(doc, null, 2) + '\n', 'utf8');
