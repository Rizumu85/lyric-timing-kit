// Search YouTube for karaoke / romaji-lyric / colour-coded-lyric (Kan/Rom/Eng) videos of the Japanese songs that still have no
// reading source. Search only, nothing is downloaded. Writes 卡拉OK视频候选.md in the batch folder.
// Usage: node search_karaoke.mjs
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { BATCH } from './kit-config.mjs';

const checked = new Set();
for (const file of ['furigana-audit.json', 'moegirl-audit.json', 'video-audit.json'])
  for (const entry of JSON.parse(fs.readFileSync(path.join(BATCH, file), 'utf8'))) if (entry.status === 'checked') checked.add(entry.key.replace(/-rf$/, ''));
const squash = text => text.normalize('NFKC').toLowerCase().replace(/[^\p{L}\p{N}]/gu, '');
const KARAOKE = /ニコカラ|カラオケ|karaoke|off ?vocal|on ?vocal|romaji|ローマ字|rom\b|lyrics|歌詞|字幕/i;
const out = ['# 卡拉 OK / 罗马音歌词视频候选（YouTube）', ''];
let hits = 0, total = 0;
for (const key of fs.readdirSync(BATCH)) {
  const file = path.join(BATCH, key, 'source.json');
  if (!fs.existsSync(file) || key.endsWith('-rf') || checked.has(key)) continue;
  const source = JSON.parse(fs.readFileSync(file, 'utf8'));
  if (source.language !== 'ja' || !source.rows?.length) continue;
  // Lyrics that spell out most of their own readings need no other source.
  const runs = source.rows.flatMap(row => row.original.match(/[㐀-鿿々]+(?:[(（][ぁ-ヿa-zA-Z]+[)）])?/g) || []);
  if (!runs.length || runs.filter(run => /[(（]/.test(run)).length >= runs.length * .8) continue;
  total++;
  const title = (source.title || '').replace(/\s*[\(（\[【].*$/, '').replace(/\s+feat\..*$/i, '').trim();
  const found = new Map();
  for (const query of [`${title} ニコカラ`, `${title} romaji lyrics`, `${title} color coded lyrics kan rom eng`]) {
    const run = spawnSync('yt-dlp', ['--flat-playlist', '--no-warnings', '--print', '%(id)s\t%(duration)s\t%(title)s', `ytsearch6:${query}`], { encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8' } });
    for (const line of (run.stdout || '').split('\n')) {
      const [id, duration, name] = line.split('\t');
      if (name && squash(name).includes(squash(title)) && KARAOKE.test(name)) found.set(id, { duration: Math.round(Number(duration) || 0), name });
    }
  }
  console.log(`${found.size ? 'hit ' : 'none'} ${source.name}`);
  if (!found.size) continue;
  hits++;
  out.push(`## ${source.name}`, `歌曲时长 ${Math.round(source.duration)} 秒 · key ${key}`, '');
  for (const [id, video] of found) out.push(`- [${video.name}](https://www.youtube.com/watch?v=${id})（${video.duration} 秒）`);
  out.push('');
}
out.splice(1, 0, '', `${total} 首没有读音来源的日语歌里，${hits} 首搜到了候选视频。`);
fs.writeFileSync(path.join(BATCH, '卡拉OK视频候选.md'), out.join('\n'), 'utf8');
console.log(`${hits} of ${total} songs have candidates`);
