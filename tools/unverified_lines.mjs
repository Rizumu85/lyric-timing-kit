// List every lyric line whose kanji readings no source vouches for (not compared by UtaTen,
// Moegirl, a karaoke video or a romaji page, and not spelled out in the lyrics), with the
// reading currently marked. For a context-aware review of the dictionary's guesses.
// Usage: node unverified_lines.mjs > lines.txt      (line format: key <tab> line number <tab> annotated text)
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { BATCH } from './kit-config.mjs';

const readJson = file => { try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch { return null; } };
const compared = new Map();
for (const file of ['furigana-audit.json', 'moegirl-audit.json', 'video-audit.json'])
  for (const entry of readJson(path.join(BATCH, file)) || []) {
    const rows = compared.get(entry.key) || new Set();
    for (const line of entry.rows || []) rows.add(line - 1);
    compared.set(entry.key, rows);
  }
const RUN = /[㐀-鿿々]+(?:[(（][ぁ-ヿa-zA-Z]+[)）])?/g;
let songs = 0, lines = 0;
for (const key of fs.readdirSync(BATCH)) {
  const source = readJson(path.join(BATCH, key, 'source.json')), prepared = readJson(path.join(BATCH, key, 'prepared.json'));
  if (!source?.rows || !prepared?.rows || source.language !== 'ja' || key.endsWith('-rf') || prepared.rows.length !== source.rows.length) continue;
  const runs = index => source.rows[index].original.match(RUN) || [];
  const known = new Set([...(compared.get(key) || [])].flatMap(index => source.rows[index] ? runs(index) : []));
  const open = source.rows.map((row, index) => index).filter(index => !compared.get(key)?.has(index) && runs(index).some(run => !/[(（]/.test(run) && !known.has(run)));
  if (!open.length) continue;
  songs++;
  console.log(`# ${key} ${source.name}`);
  for (const index of open) { lines++; console.log(`${key}\t${index + 1}\t${prepared.rows[index].annotated}`); }
}
console.error(`${songs} songs, ${lines} lines`);
