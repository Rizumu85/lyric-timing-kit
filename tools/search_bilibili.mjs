// Search Bilibili for karaoke / furigana / romaji lyric videos of the Japanese songs that still
// have no reading source. Search only. Writes B站视频候选.md in the batch folder.
// Usage: node search_bilibili.mjs
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { BATCH } from './kit-config.mjs';

const checked = new Set();
for (const file of ['furigana-audit.json', 'moegirl-audit.json', 'video-audit.json'])
  for (const entry of JSON.parse(fs.readFileSync(path.join(BATCH, file), 'utf8'))) if (entry.status === 'checked') checked.add(entry.key.replace(/-rf$/, ''));
const squash = text => text.normalize('NFKC').toLowerCase().replace(/[^\p{L}\p{N}]/gu, '');
const WANTED = /ニコカラ|カラオケ|karaoke|off ?vocal|on ?vocal|罗马音|羅馬音|romaji|假名|注音|振假名|歌词|歌詞|字幕|KTV/i;
const out = ['# B 站卡拉 OK / 注音歌词视频候选', ''];
let hits = 0, total = 0;
for (const key of fs.readdirSync(BATCH)) {
  const file = path.join(BATCH, key, 'source.json'), resultFile = path.join(BATCH, key, 'result.json');
  if (!fs.existsSync(file) || key.endsWith('-rf') || checked.has(key)) continue;
  const source = JSON.parse(fs.readFileSync(file, 'utf8'));
  if (source.language !== 'ja' || !source.rows?.length) continue;
  if (fs.existsSync(resultFile) && JSON.parse(fs.readFileSync(resultFile, 'utf8')).confirmed) continue;
  const runs = source.rows.flatMap(row => row.original.match(/[㐀-鿿々]+(?:[(（][ぁ-ヿa-zA-Z]+[)）])?/g) || []);
  if (!runs.length || runs.filter(run => /[(（]/.test(run)).length >= runs.length * .8) continue;
  total++;
  const title = (source.title || '').replace(/\s*[\(（\[【].*$/, '').replace(/\s+feat\..*$/i, '').trim();
  const found = new Map();
  for (const query of [`${title} ニコカラ`, `${title} 罗马音`, `${title} 假名`]) {
    // One quoted command line: with separate arguments the shell splits a query at its spaces.
    const run = spawnSync(`bili search "${query.replace(/["`$\\]/g, '')}" --type video -n 8`, { encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' }, shell: true });
    const text = run.stdout || '';
    for (const match of text.matchAll(/bvid: (\S+)\s+title: (.*?)\r?\n\s+author: (.*?)\r?\n\s+play: (\d+)\s+duration: '?([\d:]+)'?/g)) {
      const [, bvid, name, author, , duration] = match;
      if (squash(name).includes(squash(title)) && WANTED.test(name)) found.set(bvid, { name: name.replace(/^'|'$/g, ''), author, duration });
    }
  }
  console.log(`${found.size ? 'hit ' : 'none'} ${source.name}`);
  out.push(`## ${source.name}`, `歌曲时长 ${Math.round(source.duration)} 秒 · key ${key}`, '');
  if (!found.size) { out.push('（没有搜到）', ''); continue; }
  hits++;
  for (const [bvid, video] of found) out.push(`- ${bvid}（${video.duration}）${video.name} — ${video.author}`);
  out.push('');
}
out.splice(1, 0, '', `${total} 首没有读音来源的日语歌里，${hits} 首在 B 站搜到了候选视频。`);
fs.writeFileSync(path.join(BATCH, 'B站视频候选.md'), out.join('\n'), 'utf8');
console.log(`${hits} of ${total} songs have candidates`);
