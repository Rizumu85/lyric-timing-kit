// Write the readings found by furigana_audit.mjs into each song's source.json as 漢字(よみ).
// Prints the keys it changed, one per line, into <data>/readings-changed.txt.
// Usage: node apply_furigana.mjs
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { BATCH } from './kit-config.mjs';

// Pass another audit file (e.g. moegirl-audit.json) as the first argument to apply that one.
// This rewrites lyric sheets, so it only runs when asked to: node apply_furigana.mjs --write [audit file]
if (!process.argv.includes('--write')) { console.log('Nothing done. This writes readings into source.json; run it with --write [audit file].'); process.exit(0); }
const report = JSON.parse(await fs.readFile(path.join(BATCH, process.argv.slice(2).find(arg => !arg.startsWith('--')) || 'furigana-audit.json'), 'utf8'));
const escape = text => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
function splitGroup(group) {
  const parts = group.parts.map(([base, ours]) => ({ line: group.line, base, ours, theirs: null }));
  let rest = group.theirs, first = 0, last = parts.length - 1;
  while (last > first && rest.endsWith(parts[last].ours)) { parts[last].theirs = parts[last].ours; rest = rest.slice(0, -parts[last].ours.length); last--; }
  while (first < last && rest.startsWith(parts[first].ours)) { parts[first].theirs = parts[first].ours; rest = rest.slice(parts[first].ours.length); first++; }
  const open = parts.slice(first, last + 1), kanji = open.reduce((n, part) => n + part.base.length, 0);
  let used = 0, seen = 0;
  open.forEach((part, index) => {
    seen += part.base.length;
    const end = index === open.length - 1 ? rest.length : Math.max(used + 1, Math.round(rest.length * seen / kanji));
    part.theirs = rest.slice(used, end); used = end;
  });
  return parts.filter(part => part.theirs && part.theirs !== part.ours).map(part => ({ ...part, forced: true }));
}
const changed = [];
let applied = 0, skipped = 0;

for (const entry of report) {
  if (entry.status !== 'checked' || !entry.diffs.length) continue;
  const file = path.join(BATCH, entry.key, 'source.json');
  // Reports can mention folders that no longer exist (merged experiments) and songs the user
  // has confirmed, which must not change.
  const verdict = await fs.readFile(path.join(BATCH, entry.key, 'result.json'), 'utf8').then(JSON.parse).catch(() => null);
  if (!(await fs.stat(file).catch(() => null)) || verdict?.confirmed) continue;
  const source = JSON.parse(await fs.readFile(file, 'utf8'));
  let touched = 0;
  for (const whole of entry.diffs) {
    // A group of several kanji runs (split in the lyrics by ・ or spaces) was compared as one.
    // Runs whose own reading still fits at either end keep it; the rest share what is left,
    // divided by their number of kanji. The joined reading, which is what gets sung, is exact.
    const row = source.rows[whole.line - 1];
    // More than four kana per kanji is not a reading; the comparison swallowed a neighbouring word.
    if (!row || !whole.ours || whole.theirs.length > whole.base.length * 4 + 1) { skipped++; continue; }
    // Our tokenizer may have cut one written kanji run in two (輪|切); a reading can only be
    // attached to the run as written, so join such neighbours again before splitting the group.
    const parts = [];
    for (const [base, ours] of whole.parts || [[whole.base, whole.ours]]) {
      const previous = parts.at(-1);
      if (previous && row.original.includes(previous[0] + base)) { previous[0] += base; previous[1] += ours; } else parts.push([base, ours]);
    }
    const pieces = parts.length > 1 ? splitGroup({ ...whole, parts }) : [{ ...whole, base: parts[0][0] }];
    for (const diff of pieces) {
    // A reading already written after the kanji (the lyric sheet's own romaji) is replaced.
    const pattern = new RegExp('(?<![\\u3400-\\u9fff々])' + escape(diff.base) + '(?:[(（][ぁ-ヿa-zA-Z]+[)）])?(?![\\u3400-\\u9fff々])');
    if (!row || !pattern.test(row.original) || row.original.includes(`${diff.base}(${diff.theirs})`)) { skipped++; continue; }
    row.original = row.original.replace(pattern, `${diff.base}(${diff.theirs})`);
    touched++;
    }
  }
  if (!touched) continue;
  await fs.writeFile(file, JSON.stringify(source, null, 2), 'utf8');
  changed.push(entry.key); applied += touched;
}
await fs.writeFile(path.join(BATCH, 'readings-changed.txt'), changed.join('\n') + '\n', 'utf8');
console.log(`applied ${applied} readings in ${changed.length} songs, skipped ${skipped}`);
