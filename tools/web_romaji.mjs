// Fallback reading source: for the Japanese songs that still have none, search Google in the
// user's own browser (through Kimi WebBridge; lyric sites refuse plain requests) and open the
// results one by one — lyrical-nonsense / UtaTime, Genius romanizations, fan wikis, blogs,
// whatever comes up. From each page the lines written in Latin letters are taken, and
// romaji_match.py decides whether they are this song's lyrics by comparing them with our own
// readings. The first page that matches is saved as backups/video/<key>/readings.txt, the
// file video_audit.py compares against.
// Usage: node web_romaji.mjs [<key> ...]      (no keys: every Japanese song without a reading source)
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { wb, evaluate, sleep } from './wb.mjs';
import { BATCH, VIDEO, PYTHON } from './kit-config.mjs';

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const readJson = file => { try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch { return null; } };

const checked = new Set();
for (const file of ['furigana-audit.json', 'moegirl-audit.json', 'video-audit.json'])
  for (const entry of readJson(path.join(BATCH, file)) || []) if (entry.status === 'checked') checked.add(entry.key);
let keys = process.argv.slice(2);
if (!keys.length) keys = fs.readdirSync(BATCH).filter(key => {
  const source = readJson(path.join(BATCH, key, 'source.json'));
  if (!source?.rows?.length || source.language !== 'ja' || key.endsWith('-rf') || checked.has(key)) return false;
  const runs = source.rows.flatMap(row => row.original.match(/[㐀-鿿々]+(?:[(（][ぁ-ヿa-zA-Z]+[)）])?/g) || []);
  return runs.length && runs.filter(run => /[(（]/.test(run)).length < runs.length * .8;
});

// pages that never carry romaji lyrics
const SKIP = /youtube\.com|youtu\.be|spotify\.com|music\.apple\.com|uta-net\.com|petitlyrics\.com|amazon\.|bilibili\.com|nicovideo\.jp|twitter\.com|x\.com|facebook\.com|instagram\.com|tiktok\.com|wikipedia\.org/;
const RESULTS = `JSON.stringify([...document.querySelectorAll('a h3')].map(h => [h.closest('a').href, h.textContent]))`;
// every line of the page that is written in Latin letters
const LATIN = `JSON.stringify({ title: document.title, lines: document.body.innerText.split(/[\\n\\t]/).map(line => line.trim()).filter(line => {
  const letters = line.split('').filter(char => char.trim()).length, latin = (line.match(/[A-Za-zāīūēōâîûêô']/g) || []).length;
  return line.length >= 4 && line.length <= 200 && latin >= letters * 0.8 && line.split(' ').length <= 30;
}).slice(0, 600) })`;
const scratch = path.join(BATCH, 'romaji-candidate.txt');
let saved = 0, first = true;
songs: for (const key of keys) {
  const source = readJson(path.join(BATCH, key, 'source.json'));
  const title = (source.title || '').replace(/\s*\[[^\]]*\]\s*$/, '').replace(/\s*[\(（].*$/, '').trim();
  const artist = (source.artist || source.name.split(' - ')[0]).split(/[×x&]/)[0].trim();
  await wb('navigate', { url: 'https://www.google.com/search?q=' + encodeURIComponent(`${title} ${artist} romaji lyrics`), ...(first ? { newTab: true, group_title: '歌词读音来源' } : {}) });
  first = false;
  await sleep(5000 + Math.random() * 3000);
  if (/\/sorry\//.test(await evaluate('location.href'))) { console.log('Google 要求人机验证，停止。'); break; }
  const results = JSON.parse(await evaluate(RESULTS)).filter(([url]) => /^https?:/.test(url) && !SKIP.test(url)).slice(0, 5);
  let best = { share: 0 };
  for (const [url] of results) {
    let page;
    try {
      await wb('navigate', { url });
      await sleep(4500);
      page = JSON.parse(await evaluate(LATIN));
    } catch { continue; }
    if (/just a moment|attention required|verify you are human/i.test(page.title)) continue;
    if (page.lines.length < 8) continue;
    fs.writeFileSync(scratch, page.lines.join('\n'), 'utf8');
    const run = spawnSync(PYTHON, ['romaji_match.py', key, scratch], { cwd: ROOT, encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8' } });
    const verdict = readJsonText(run.stdout);
    if (verdict && verdict.share > best.share) best = { ...verdict, url };
    if (verdict?.share >= 0.6) {
      spawnSync(PYTHON, ['romaji_match.py', key, scratch, '--save'], { cwd: ROOT, encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8' } });
      fs.writeFileSync(path.join(VIDEO, key, 'readings-source.txt'), url + '\n', 'utf8');
      saved++;
      console.log(`saved    ${source.name} (${Math.round(verdict.share * 100)}% of our lines, ${verdict.lines} lines) ${url}`);
      continue songs;
    }
  }
  console.log(`none     ${source.name}${best.share ? ` (best ${Math.round(best.share * 100)}% at ${best.url})` : ''}`);
}
fs.rmSync(scratch, { force: true });
await wb('close_session').catch(() => {});
console.log(`${saved} of ${keys.length} songs got romaji`);

function readJsonText(text) { try { return JSON.parse((text || '').trim().split('\n').pop()); } catch { return null; } }
