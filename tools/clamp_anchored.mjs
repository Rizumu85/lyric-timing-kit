// After `realign_song.py --anchor-embedded`: a line whose first syllable strayed far before the
// line start that is already in the MP3 gets clamped back to that start (the rest of the line
// was aligned inside its window and is left alone). Clears the "offset over 8 s" verdict when
// that was the only complaint and the clamp removed its cause.
// Usage: node clamp_anchored.mjs <key> [<key> ...] | --keys-file <file>
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { BATCH } from './kit-config.mjs';

const args = process.argv.slice(2);
const keys = args[0] === '--keys-file' ? fs.readFileSync(args[1], 'utf8').split(/\s+/).filter(Boolean) : args;
const OFFSET = /句首偏移8秒以上/;
const clamp = (node, floor) => {
  for (const key of ['startTime', 'endTime']) if (typeof node[key] === 'number' && node[key] < floor) node[key] = floor;
  for (const key of ['words', 'syllables', 'ruby']) for (const child of node[key] || []) clamp(child, floor);
};
for (const key of keys) {
  const at = name => path.join(BATCH, key, name);
  if (!fs.existsSync(at('aligned.bilingual.lrc')) || !fs.existsSync(at('aligned.fia'))) continue;
  const starts = [...new Set([...fs.readFileSync(at('aligned.bilingual.lrc'), 'utf8').matchAll(/^\[(\d+):(\d+)\.(\d+)\]/gm)].map(m => Math.round((Number(m[1]) * 60 + Number(m[2]) + Number(m[3]) / 100 + 0.05) * 100) / 100))].sort((a, b) => a - b);
  const doc = JSON.parse(fs.readFileSync(at('aligned.fia'), 'utf8')), lines = doc.lyrics.lines;
  if (starts.length !== lines.length) continue;
  let clamped = 0, far = 0;
  lines.forEach((line, index) => {
    if (line.startTime < starts[index] - 1) { clamp(line, starts[index]); clamped++; }
    if (Math.abs(line.startTime - starts[index]) > 1) far++;
  });
  if (!clamped) continue;
  fs.writeFileSync(at('aligned.fia'), JSON.stringify(doc, null, 2), 'utf8');
  const result = JSON.parse(fs.readFileSync(at('result.json'), 'utf8'));
  const problems = result.issues.filter(issue => /需检查|需核对|需试听确认|可能文字错配|越界/.test(issue));
  if (result.status === 'needs_review' && !far && problems.every(issue => OFFSET.test(issue))) {
    result.status = 'aligned';
    result.issues = result.issues.filter(issue => !OFFSET.test(issue));
  }
  fs.writeFileSync(at('result.json'), JSON.stringify(result, null, 2), 'utf8');
  console.log(`${result.name}: clamped ${clamped} line(s), status ${result.status}`);
}
