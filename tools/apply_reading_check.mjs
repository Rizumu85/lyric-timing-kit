// Use the results of reading_check.py: readings the audio clearly supports (gain >= 0.08) go
// into source.json; weaker ones are only flagged. Every touched line gets a note for the
// listening page in <key>/reading-flags.json. Writes readings-changed.txt for realign_song.py.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { BATCH } from './kit-config.mjs';

// This rewrites lyric sheets, so it only runs when asked to: node apply_reading_check.mjs --write
if (!process.argv.includes('--write')) { console.log('Nothing done. This writes readings into source.json and reading-flags.json; run it with --write.'); process.exit(0); }
const SURE = 0.08, MAYBE = 0.04;
const escape = text => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const changed = [];
let applied = 0, flagged = 0;
for (const key of fs.readdirSync(BATCH)) {
  const file = path.join(BATCH, key, 'reading-check.json');
  if (!fs.existsSync(file)) continue;
  const findings = JSON.parse(fs.readFileSync(file, 'utf8')).filter(finding => finding.gain >= MAYBE);
  const sourceFile = path.join(BATCH, key, 'source.json'), source = JSON.parse(fs.readFileSync(sourceFile, 'utf8'));
  const flags = [];
  let touched = false;
  for (const finding of findings) {
    const row = source.rows[finding.line - 1];
    if (!row) continue;
    if (finding.gain >= SURE) {
      const pattern = new RegExp('(?<![\\u3400-\\u9fff々])' + escape(finding.base) + '(?![(（\\u3400-\\u9fff々])');
      if (!pattern.test(row.original)) continue;
      row.original = row.original.replace(pattern, `${finding.base}(${finding.heard})`);
      flags.push({ line: finding.line, text: finding.text, flag: `声学判断：「${finding.base}」${finding.default} → ${finding.heard}` });
      touched = true; applied++;
    } else {
      flags.push({ line: finding.line, text: finding.text, flag: `读音可疑：「${finding.base}」现在读${finding.default}，听起来也像${finding.heard}` });
      flagged++;
    }
  }
  if (flags.length) fs.writeFileSync(path.join(BATCH, key, 'reading-flags.json'), JSON.stringify(flags, null, 2), 'utf8');
  if (touched) { fs.writeFileSync(sourceFile, JSON.stringify(source, null, 2), 'utf8'); changed.push(key); }
}
fs.writeFileSync(path.join(BATCH, 'readings-changed.txt'), changed.join('\n') + '\n', 'utf8');
console.log(`changed ${applied} readings in ${changed.length} songs; flagged ${flagged} more as doubtful`);
