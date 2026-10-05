// Look on NetEase and QQ Music for an established Chinese translation of a song that has none,
// and save every candidate that carries one as <key>/translation-candidates.json.
// Nothing is applied to the song; the lines are matched and applied by apply_translation.mjs.
// Usage: node find_translation.mjs <key> [<key> ...]
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { BATCH } from './kit-config.mjs';

const UA = { 'User-Agent': 'Mozilla/5.0' };
const plain = title => (title || '').replace(/\s*\[[^\]]*\]\s*$/, '').trim();
const json = async (url, headers = {}) => { try { return await (await fetch(url, { headers: { ...UA, ...headers } })).json(); } catch { return null; } };
// "[mm:ss.xx]text" lines -> [[seconds, text], ...]
const parse = lrc => (lrc || '').split(/\r?\n/).flatMap(line => {
  const match = /^\[(\d+):(\d+(?:\.\d+)?)\](.*)$/.exec(line.trim());
  return match && match[3].trim() ? [[Number(match[1]) * 60 + Number(match[2]), match[3].trim()]] : [];
});

async function netease(query) {
  const found = await json('https://music.163.com/api/cloudsearch/pc?type=1&limit=8&s=' + encodeURIComponent(query), { Referer: 'https://music.163.com/' });
  const out = [];
  for (const song of found?.result?.songs || []) {
    const lyric = await json(`https://music.163.com/api/song/lyric?id=${song.id}&lv=1&tv=-1`, { Referer: 'https://music.163.com/' });
    const lines = parse(lyric?.tlyric?.lyric);
    if (lines.length) out.push({ platform: '网易云', title: song.name, artist: (song.ar || []).map(a => a.name).join('/'), seconds: Math.round(song.dt / 1000), original: parse(lyric?.lrc?.lyric), translation: lines });
  }
  return out;
}

async function qq(query) {
  const found = await json('https://c.y.qq.com/soso/fcgi-bin/client_search_cp?format=json&n=8&w=' + encodeURIComponent(query), { Referer: 'https://y.qq.com/' });
  const out = [];
  for (const song of found?.data?.song?.list || []) {
    const lyric = await json(`https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg?format=json&nobase64=1&songmid=${song.songmid}`, { Referer: 'https://y.qq.com/' });
    const lines = parse(lyric?.trans);
    if (lines.length) out.push({ platform: 'QQ音乐', title: song.songname, artist: (song.singer || []).map(a => a.name).join('/'), seconds: song.interval, original: parse(lyric?.lyric), translation: lines });
  }
  return out;
}

for (const key of process.argv.slice(2)) {
  const source = JSON.parse(fs.readFileSync(path.join(BATCH, key, 'source.json'), 'utf8'));
  const title = plain(source.title), candidates = [];
  for (const query of [`${title} ${source.artist || ''}`.trim(), title]) {
    for (const search of [netease, qq]) for (const hit of await search(query))
      if (!candidates.some(old => old.platform === hit.platform && old.title === hit.title && old.artist === hit.artist)) candidates.push(hit);
    if (candidates.length) break;
  }
  fs.writeFileSync(path.join(BATCH, key, 'translation-candidates.json'), JSON.stringify(candidates, null, 2), 'utf8');
  console.log(`${source.name} (${Math.round(source.duration)} s): ${candidates.length} candidate(s)`);
  for (const hit of candidates) console.log(`   ${hit.platform} | ${hit.title} | ${hit.artist} | ${hit.seconds} s | ${hit.translation.length} translated lines`);
}
