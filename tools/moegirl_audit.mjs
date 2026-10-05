// Second reading source: Moegirl lyric pages mark the non-dictionary readings (写作 A 读作 B).
// For Japanese songs UtaTen did not cover, find the page by song title, confirm it is the same
// lyrics, and collect the marked readings per line in the same shape furigana_audit.mjs writes.
// Writes <data>/moegirl-audit.json.   Usage: node moegirl_audit.mjs [key ...]
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { BATCH, CACHE as CACHE_DIR } from './kit-config.mjs';

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const CACHE = path.join(CACHE_DIR, 'moegirl-cache');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const readJson = async file => { try { return JSON.parse(await fs.readFile(file, 'utf8')); } catch { return null; } };
const hira = text => text.replace(/[ァ-ヶ]/g, c => String.fromCharCode(c.charCodeAt(0) - 96));
const decode = text => text.replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#(\d+);/g, (_, n) => String.fromCodePoint(n)).replace(/&nbsp;/g, ' ');
const squash = text => hira(text.normalize('NFKC')).toLowerCase().replace(/[^ぁ-ゖー㐀-鿿々a-z0-9]/g, '');
const strip = html => decode(html.replace(/<rt[\s\S]*?<\/rt>/g, '').replace(/<span class="template-ruby-hidden">[\s\S]*?<\/span>/g, '').replace(/<[^>]+>/g, ''));

async function get(title) {
  const file = path.join(CACHE, encodeURIComponent(title).slice(0, 150).replace(/[*]/g, '_') + '.html');
  try { return await fs.readFile(file, 'utf8'); } catch {}
  await sleep(1500);
  const response = await fetch('https://zh.moegirl.org.cn/' + encodeURIComponent(title), { headers: { 'User-Agent': 'Mozilla/5.0' } });
  const html = response.ok ? await response.text() : '';
  await fs.mkdir(CACHE, { recursive: true });
  await fs.writeFile(file, html, 'utf8');
  return html;
}

async function audit(key, covered) {
  const source = await readJson(path.join(BATCH, key, 'source.json'));
  const prepared = await readJson(path.join(BATCH, key, 'prepared.json'));
  const result = await readJson(path.join(BATCH, key, 'result.json'));
  if (!source || !prepared || source.language !== 'ja' || covered.has(key)) return null;
  const entry = { key, name: source.name, written: result?.status === 'complete' };
  const titles = [...new Set([source.title, (source.title || '').replace(/\s*[\(（\[【].*$/, '').replace(/\s+feat\..*$/i, '').trim()].filter(Boolean))];
  const rows = prepared.rows.map((row, index) => ({ index, text: squash(row.original || ''), row })).filter(item => item.text.length > 3);
  for (const title of titles) {
    const html = await get(title);
    if (!html) continue;
    // Lyric lines end at <br>, a table cell, or a paragraph; keep each piece with its markup.
    const pieces = html.split(/<br\s*\/?>|<\/td>|<\/p>|<\/div>|\n/);
    const byText = new Map(pieces.map(piece => [squash(strip(piece)), piece]));
    const matched = rows.filter(item => byText.has(item.text));
    if (matched.length < rows.length * .3) continue;
    const diffs = [];
    for (const item of matched) {
      for (const ruby of byText.get(item.text).matchAll(/<ruby[^>]*>([\s\S]*?)<rt[^>]*>([\s\S]*?)<\/rt>/g)) {
        const base = strip(ruby[1]).trim(), theirs = hira(strip(ruby[2]).trim());
        if (!/^[㐀-鿿々]+$/.test(base) || !/^[ぁ-ゖー]+$/.test(theirs)) continue; // only kanji read as kana can be applied
        const ours = new RegExp('\\{' + base + '\\|([^}]+)\\}').exec(item.row.annotated || '')?.[1] ?? '';
        if (hira(ours) !== theirs) diffs.push({ line: item.index + 1, text: item.row.original, base, ours: hira(ours), theirs });
      }
    }
    return { ...entry, status: 'checked', url: 'https://zh.moegirl.org.cn/' + encodeURIComponent(title), share: Math.round(matched.length / rows.length * 100) / 100, rows: matched.map(item => item.index + 1), diffs };
  }
  return { ...entry, status: 'not-found' };
}

const utaten = (await readJson(path.join(BATCH, 'furigana-audit.json'))) || [];
const covered = new Set(utaten.filter(entry => entry.status === 'checked').map(entry => entry.key));
const keys = process.argv.slice(2);
const folders = keys.length ? keys : (await fs.readdir(BATCH, { withFileTypes: true })).filter(e => e.isDirectory()).map(e => e.name);
const report = [];
for (const key of folders) {
  try { const entry = await audit(key, keys.length ? new Set() : covered); if (entry) { report.push(entry); console.log(`${entry.status.padEnd(9)} ${entry.diffs ? String(entry.diffs.length).padStart(2) + ' 处' : '     '} ${entry.name}`); } }
  catch (error) { console.log(`error     ${key} ${error.message}`); }
}
// A run on selected keys updates those entries and keeps the rest of the report.
if (keys.length) {
  const fresh = new Set(report.map(entry => entry.key));
  report.push(...((await readJson(path.join(BATCH, 'moegirl-audit.json'))) || []).filter(entry => !fresh.has(entry.key)));
}
await fs.writeFile(path.join(BATCH, 'moegirl-audit.json'), JSON.stringify(report, null, 2), 'utf8');
const checked = report.filter(entry => entry.status === 'checked');
console.log(`\nfound ${checked.length} of ${report.length}, readings to fix ${checked.reduce((n, e) => n + e.diffs.length, 0)}`);
