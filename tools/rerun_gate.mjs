// Decide which re-aligned songs are safe to write back without listening: the new draft must
// pass the aligner's own checks and stay close to the lyrics already embedded in the MP3.
// Writes rerun-ready.txt (keys to embed) and rerun-held.json (songs that need a listen).
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { BATCH } from './kit-config.mjs';

const ready = [], held = [];
for (const key of fs.readdirSync(BATCH)) {
  const at = name => path.join(BATCH, key, name);
  if (!fs.existsSync(at('result.json')) || !fs.existsSync(at('aligned.bilingual.lrc'))) continue;
  const result = JSON.parse(fs.readFileSync(at('result.json'), 'utf8'));
  if (result.wasStatus !== 'complete' || result.status === 'complete' || result.confirmed) continue;
  const old = [...fs.readFileSync(at('aligned.bilingual.lrc'), 'utf8').matchAll(/^\[(\d+):(\d+)\.(\d+)\](.*)$/gm)]
    .map(m => [Number(m[1]) * 60 + Number(m[2]) + Number(m[3]) / 100 + 0.05, m[4]]); // embedded times carry a 50 ms lead
  const lines = JSON.parse(fs.readFileSync(at('aligned.fia'), 'utf8')).lyrics.lines;
  let cursor = 0, matched = 0, over1 = 0, over3 = 0;
  // When only the spelling of the lyrics changed (kanji corrected), the lines still correspond
  // one to one: pair them by position instead of by text.
  const starts = [...new Set(old.map(row => row[0]))];
  const byPosition = starts.length === lines.length;
  for (const [index, line] of lines.entries()) {
    let j = cursor;
    if (byPosition) j = old.findIndex(row => row[0] === starts[index]);
    else { while (j < old.length && old[j][1] !== line.fullText) j++; }
    if (j < 0 || j >= old.length) continue;
    cursor = j + 1; matched++;
    const moved = Math.abs(line.startTime - old[j][0]);
    if (moved > 1) over1++;
    if (moved > 3) over3++;
  }
  const reasons = [];
  if (result.status !== 'aligned') reasons.push('自动检查未通过');
  if (matched < lines.length * .9) reasons.push('和已写入的歌词对不上行');
  if (over3) reasons.push(`${over3} 行比已写入的版本移动了 3 秒以上`);
  if (over1 > Math.max(2, matched * .1)) reasons.push(`${over1} 行比已写入的版本移动了 1 秒以上`);
  if (reasons.length) held.push({ key, name: result.name, reasons }); else ready.push(key);
}
fs.writeFileSync(path.join(BATCH, 'rerun-ready.txt'), ready.join('\n') + '\n', 'utf8');
fs.writeFileSync(path.join(BATCH, 'rerun-held.json'), JSON.stringify(held, null, 2), 'utf8');
console.log(`ready ${ready.length}, held ${held.length}`);
