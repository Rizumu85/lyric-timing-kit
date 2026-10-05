// Fill a song's missing translations from the candidates saved by find_translation.mjs.
// Picks the candidate with the same title and the closest duration, and pairs its translated
// lines with the song's rows by time (the rows keep the platform's own line times in sourceStart).
// Rows that already have a translation are left alone. Usage: node apply_translation.mjs <key> [<key> ...]
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { BATCH } from './kit-config.mjs';

const squash = text => (text || '').normalize('NFKC').toLowerCase().replace(/\[[^\]]*\]$/, '').replace(/[^\p{L}\p{N}]/gu, '');
const readJson = file => JSON.parse(fs.readFileSync(file, 'utf8'));

for (const key of process.argv.slice(2)) {
  const folder = path.join(BATCH, key), source = readJson(path.join(folder, 'source.json'));
  const candidates = readJson(path.join(folder, 'translation-candidates.json'))
    .filter(hit => squash(hit.title) === squash(source.title) && Math.abs(hit.seconds - source.duration) <= 3)
    .sort((a, b) => Math.abs(a.seconds - source.duration) - Math.abs(b.seconds - source.duration));
  if (!candidates.length) { console.log(`${source.name}: no candidate with this title and length`); continue; }
  const hit = candidates[0];
  let filled = 0, missing = [];
  source.rows.forEach((row, index) => {
    if (row.translation) return;
    const near = hit.translation.filter(([time]) => Math.abs(time - row.sourceStart) <= 0.6).sort((a, b) => Math.abs(a[0] - row.sourceStart) - Math.abs(b[0] - row.sourceStart))[0];
    if (near) { row.translation = near[1]; filled++; } else missing.push(index + 1);
  });
  source.issues = (source.issues || []).filter(issue => !/缺中文翻译/.test(issue));
  if (missing.length) source.issues.push(`外语歌词缺中文翻译：${missing.length}/${source.rows.length}行`);
  fs.writeFileSync(path.join(folder, 'source.json'), JSON.stringify(source, null, 2), 'utf8');
  for (const name of ['aligned.fia', 'result.json']) {
    const file = path.join(folder, name);
    if (!fs.existsSync(file)) continue;
    const data = readJson(file);
    if (name === 'aligned.fia' && data.lyrics.lines.length === source.rows.length) data.lyrics.lines.forEach((line, index) => { if (source.rows[index].translation) line.translation = source.rows[index].translation; });
    if (name === 'result.json') data.issues = [...(data.issues || []).filter(issue => !/缺中文翻译/.test(issue)), ...source.issues.filter(issue => /缺中文翻译/.test(issue))];
    fs.writeFileSync(file, JSON.stringify(data, null, 2), 'utf8');
  }
  console.log(`${source.name}: ${hit.platform} 的译文，补了 ${filled} 行${missing.length ? `，还缺第 ${missing.join('、')} 行` : ''}`);
}
