// Check the dictionary readings of every Japanese song against the furigana on UtaTen.
// For each song: search UtaTen by title, pick the page whose lyrics match ours, then compare
// line by line. Writes <data>/furigana-audit.json and 读音核对.md.
// Usage: node furigana_audit.mjs [key ...]      (no keys = every Japanese song)
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { BATCH, CACHE as CACHE_DIR } from './kit-config.mjs';

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const CACHE = path.join(CACHE_DIR, 'utaten-cache');
const KANJI = /[㐀-鿿々]/;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const readJson = async file => { try { return JSON.parse(await fs.readFile(file, 'utf8')); } catch { return null; } };
const hira = text => text.replace(/[ァ-ヶ]/g, c => String.fromCharCode(c.charCodeAt(0) - 96));
const decode = text => text.replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#0?39;/g, "'").replace(/&#(\d+);/g, (_, n) => String.fromCodePoint(n)).replace(/&nbsp;/g, ' ');
// Keep only what is sung and comparable: kana (as hiragana), kanji, latin letters, digits.
const squash = text => hira(text.normalize('NFKC')).toLowerCase().replace(/[^ぁ-ゖー㐀-鿿々a-z0-9]/g, '');

async function get(url) {
  const file = path.join(CACHE, encodeURIComponent(url).slice(0, 180) + '.html');
  try { return await fs.readFile(file, 'utf8'); } catch {}
  await sleep(700);
  const response = await fetch(url, { headers: { 'User-Agent': 'Mozilla/5.0' } });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const html = await response.text();
  await fs.mkdir(CACHE, { recursive: true });
  await fs.writeFile(file, html, 'utf8');
  return html;
}

// A lyric page as one running text, so our lines are found even where the site breaks lines differently.
// kanaOf(start, end) gives that stretch with kanji replaced by furigana, or null if it cuts a ruby group.
function pageStream(html) {
  const body = /<div class="hiragana"[^>]*>([\s\S]*?)<\/div>/.exec(html)?.[1];
  if (!body) return null;
  let base = '';
  const owner = [], kana = [];
  for (const match of body.matchAll(/<span class="rb">([^<]*)<\/span>\s*<span class="rt">([^<]*)<\/span>|<[^>]+>|([^<]+)/g)) {
    if (match[1] !== undefined) {
      const group = squash(decode(match[1]));
      if (!group) continue;
      kana.push(squash(decode(match[2])));
      for (let i = 0; i < group.length; i++) owner.push(kana.length - 1);
      base += group;
    } else if (match[3]) {
      for (const char of squash(decode(match[3]))) { kana.push(char); owner.push(kana.length - 1); base += char; }
    }
  }
  return {
    base,
    kanaOf(start, end) {
      if ((start > 0 && owner[start - 1] === owner[start]) || (end < owner.length && owner[end - 1] === owner[end])) return null;
      let out = '', last = -1;
      for (let i = start; i < end; i++) if (owner[i] !== last) { out += kana[owner[i]]; last = owner[i]; }
      return out;
    },
  };
}
// Find each of our lines in the page text, walking forward so repeated lines map to their own occurrence.
function locate(stream, rows) {
  const found = new Map();
  let cursor = 0;
  rows.forEach((row, index) => {
    const text = squash(row.original || '');
    if (text.length < 2) return;
    let at = stream.base.indexOf(text, cursor);
    if (at < 0) at = stream.base.indexOf(text);
    if (at < 0) return;
    const kana = stream.kanaOf(at, at + text.length);
    if (kana !== null) found.set(index, kana);
    cursor = at + text.length;
  });
  return found;
}

// Where our reading of a kanji run differs from the site's, return [{ base, ours, theirs }].
function lineDiffs(annotated, siteKana) {
  const runs = [];
  let pattern = '';
  for (const part of annotated.split(/(\{[^|{}]+\|[^{}]+\})/)) {
    const match = /^\{([^|]+)\|([^}]+)\}$/.exec(part);
    const literal = match ? '' : squash(part);
    // Kanji runs with nothing sung between them are compared as one, since their split is ambiguous.
    if (match && runs.length && pattern.endsWith('(.+?)')) { runs.at(-1).base += match[1]; runs.at(-1).ours += hira(match[2]); runs.at(-1).parts.push([match[1], hira(match[2])]); }
    else if (match) { runs.push({ base: match[1], ours: hira(match[2]), parts: [[match[1], hira(match[2])]] }); pattern += '(.+?)'; }
    else pattern += literal.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  }
  if (!runs.length) return [];
  // Each kanji run first tries our own reading, so a run only counts as different when the
  // line cannot be read our way (a bare lazy match would split 笑顔が並び at the wrong が).
  let next = 0;
  pattern = pattern.replace(/\(\.\+\?\)/g, () => '(' + runs[next++].ours.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '|.+?)');
  const found = new RegExp('^' + pattern + '$').exec(siteKana);
  if (!found) return null; // the line is worded differently on the site
  return runs.map((run, index) => ({ ...run, theirs: found[index + 1] }))
    .filter(run => run.theirs !== run.ours && !KANJI.test(run.theirs) && /^[ぁ-ゖー]+$/.test(run.theirs));
}

async function audit(key) {
  const source = await readJson(path.join(BATCH, key, 'source.json'));
  const prepared = await readJson(path.join(BATCH, key, 'prepared.json'));
  const result = await readJson(path.join(BATCH, key, 'result.json'));
  if (!source || !prepared || source.language !== 'ja') return null;
  const entry = { key, name: source.name, written: result?.status === 'complete' };
  const rows = prepared.rows.filter(row => /\{[^|]+\|/.test(row.annotated || ''));
  if (!rows.length) return { ...entry, status: 'no-kanji' };

  const title = (source.title || '').replace(/\s*[\(（\[【].*$/, '').replace(/\s+feat\..*$/i, '').trim();
  // Search by title first; covers and translated titles are found by a line of the lyrics instead.
  const phrase = rows.map(row => row.original.split(/[\s　（(「]/).sort((a, b) => b.length - a.length)[0]).sort((a, b) => b.length - a.length)[0];
  const queries = ['title=' + encodeURIComponent(title), 'body=' + encodeURIComponent(phrase)];
  let best = null, ids = [];
  for (const query of queries) {
    const search = await get('https://utaten.com/search?sort=popular_sort_asc&' + query);
    ids = [...new Set([...search.matchAll(/href="\/lyric\/([a-z0-9]+)\/?"/g)].map(match => match[1]))].slice(0, 5);
    for (const id of ids) {
      const stream = pageStream(await get(`https://utaten.com/lyric/${id}/`));
      if (!stream) continue;
      const found = locate(stream, prepared.rows);
      const share = rows.filter(row => found.has(prepared.rows.indexOf(row))).length / rows.length;
      if (!best || share > best.share) best = { id, share, found };
      if (share > .9) break;
    }
    if (best?.share >= .5) break;
  }
  if (!best || best.share < .5) return { ...entry, status: 'not-found', title, candidates: ids.length, share: best?.share ?? 0 };

  const diffs = [];
  let compared = 0, reworded = 0;
  const lines = []; // the rows whose readings were actually compared with the page
  prepared.rows.forEach((row, index) => {
    const kana = best.found.get(index);
    if (kana === undefined || !/\{[^|]+\|/.test(row.annotated || '')) return;
    const found = lineDiffs(row.annotated, kana);
    if (found === null) { reworded++; return; }
    compared++; lines.push(index + 1);
    for (const diff of found) diffs.push({ line: index + 1, text: row.original, ...diff });
  });
  return { ...entry, status: 'checked', url: `https://utaten.com/lyric/${best.id}/`, share: Math.round(best.share * 100) / 100, compared, reworded, rows: lines, diffs };
}

const keys = process.argv.slice(2);
const folders = keys.length ? keys : (await fs.readdir(BATCH, { withFileTypes: true })).filter(e => e.isDirectory()).map(e => e.name);
const report = [];
for (const key of folders) {
  try { const entry = await audit(key); if (entry) { report.push(entry); console.log(`${entry.status.padEnd(9)} ${entry.diffs ? String(entry.diffs.length).padStart(3) + ' 处' : '      '} ${entry.name}`); } }
  catch (error) { report.push({ key, status: 'error', error: error.message }); console.log(`error     ${key} ${error.message}`); }
}
report.sort((a, b) => (b.diffs?.length || 0) - (a.diffs?.length || 0));
// A run on selected keys updates those entries and keeps the rest of the report.
if (keys.length) {
  const fresh = new Set(report.map(entry => entry.key));
  report.push(...((await readJson(path.join(BATCH, 'furigana-audit.json'))) || []).filter(entry => !fresh.has(entry.key)));
}
await fs.writeFile(path.join(BATCH, 'furigana-audit.json'), JSON.stringify(report, null, 2), 'utf8');

const checked = report.filter(entry => entry.status === 'checked');
const md = ['# 读音核对（对照 UtaTen 的振假名）', '',
  `核对到 ${checked.length} 首，其中 ${checked.filter(e => e.diffs.length).length} 首有读音不一致，共 ${checked.reduce((n, e) => n + e.diffs.length, 0)} 处。`,
  `没在 UtaTen 找到对应歌词的 ${report.filter(e => e.status === 'not-found').length} 首。`, ''];
for (const entry of checked.filter(e => e.diffs.length)) {
  md.push(`## ${entry.name}${entry.written ? '' : '（未写入）'}`, `[UtaTen](${entry.url}) · 歌词吻合 ${Math.round(entry.share * 100)}%`, '');
  for (const diff of entry.diffs) md.push(`- 第 ${diff.line} 行 **${diff.base}**：${diff.ours} → ${diff.theirs}　${diff.text}`);
  md.push('');
}
md.push('## 没找到的歌', '', ...report.filter(e => e.status === 'not-found').map(e => `- ${e.name}`));
await fs.writeFile(path.join(BATCH, '读音核对.md'), md.join('\n'), 'utf8');
console.log(`\nchecked ${checked.length}, with differences ${checked.filter(e => e.diffs.length).length}, total ${checked.reduce((n, e) => n + e.diffs.length, 0)}, not found ${report.filter(e => e.status === 'not-found').length}`);
