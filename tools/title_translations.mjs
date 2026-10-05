// For songs whose Japanese title has kana but no [translation], look up the translated title
// that NetEase Cloud Music shows for the same song. Nothing is renamed; the result is a table
// for the user to approve: <data>/歌名翻译建议.md
// Usage: node title_translations.mjs
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { BATCH, MUSIC } from './kit-config.mjs';

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const squash = text => text.normalize('NFKC').toLowerCase().replace(/[^\p{L}\p{N}]/gu, '');

async function search(query) {
  const response = await fetch('https://music.163.com/api/cloudsearch/pc?type=1&limit=10&s=' + encodeURIComponent(query), { headers: { 'User-Agent': 'Mozilla/5.0', Referer: 'https://music.163.com/' } });
  const json = await response.json().catch(() => null);
  return json?.result?.songs || [];
}

const rows = [];
const AUDIO = /\.(mp3|flac|m4a|wav|ogg|opus|aac|alac|ape|wv|tta|wma|aif|aiff)$/i;
for (const file of fs.readdirSync(MUSIC).filter(name => AUDIO.test(name)).sort()) {
  const stem = file.replace(AUDIO, '');
  const [artist, ...rest] = stem.split(' - ');
  const title = rest.join(' - ').trim() || stem;
  if (!/[ぁ-ヿ]/.test(title) || /\[.+\]/.test(title)) continue;
  const bare = title.replace(/\s*[\(（].*?[\)）]\s*/g, ' ').replace(/\s+feat\..*$/i, '').trim();
  await sleep(600);
  const songs = await search(`${bare} ${artist.split(/[×x]/)[0].trim()}`).catch(() => []);
  const wanted = squash(bare);
  // the same title; prefer an entry that carries a translated name
  const same = songs.filter(song => squash(song.name) === wanted || squash(song.name).includes(wanted) || wanted.includes(squash(song.name)));
  const hit = same.find(song => song.tns?.length) || same[0];
  const suggestion = hit?.tns?.[0] || '';
  rows.push({ file: stem, title: bare, suggestion, by: hit ? hit.ar?.map(a => a.name).join('/') : '', found: !!hit });
  console.log(`${suggestion ? 'ok  ' : hit ? 'none' : 'miss'} ${stem}  ${suggestion}`);
}
const out = ['# 歌名翻译建议（来自网易云的译名）', '', '只是建议，还没有改任何文件。空着的是网易云没有译名的，需要另查或由你决定。', '', '| 文件 | 网易云译名 | 匹配到的歌手 |', '|---|---|---|',
  ...rows.map(row => `| ${row.file} | ${row.suggestion || (row.found ? '（无译名）' : '（没搜到）')} | ${row.by} |`)];
fs.writeFileSync(path.join(BATCH, '歌名翻译建议.md'), out.join('\n'), 'utf8');
fs.writeFileSync(path.join(BATCH, 'title-translations.json'), JSON.stringify(rows, null, 2), 'utf8');
console.log(`${rows.filter(row => row.suggestion).length} of ${rows.length} titles have a NetEase translation`);
