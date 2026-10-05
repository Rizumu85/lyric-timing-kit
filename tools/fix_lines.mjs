// Change single lines of a song the user has already listened to, without moving anything else:
//   replace  – new text or reading for a line; only that line is re-aligned, and it keeps its start
//   remove / removeFrom – lines that are not sung in this recording
// The user's timing pins stay on their lines. Usage: node fix_lines.mjs <edits.json>
//   { "<key>": { "replace": [[line, "from", "to"], ...], "remove": [line, ...], "removeFrom": line } }
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { BATCH, PYTHON } from './kit-config.mjs';

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const read = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const write = (file, data) => fs.writeFileSync(file, JSON.stringify(data, null, 2), 'utf8');
const round = value => Math.round(value * 100) / 100;

for (const [key, job] of Object.entries(read(process.argv[2]))) {
  const at = name => path.join(BATCH, key, name);
  const source = read(at('source.json')), old = read(at('aligned.fia'));
  if (old.lyrics.lines.length !== source.rows.length) throw new Error(key + ': draft and lyric sheet differ in length');
  const replaced = [];
  for (const [line, from, to] of job.replace || []) {
    const row = source.rows[line - 1];
    if (!row.original.includes(from)) throw new Error(`${key} line ${line}: "${from}" not in "${row.original}"`);
    row.original = row.original.replace(from, to);
    replaced.push(line - 1);
  }
  if (replaced.length) {
    // align the whole song once with the new text, then take only the changed lines from it
    const keep = ['result.json', 'timing-edits.json'].filter(name => fs.existsSync(at(name))).map(name => [name, fs.readFileSync(at(name))]);
    const result = read(at('result.json'));
    write(at('source.json'), source);
    const written = result.status === 'complete' || result.wasStatus === 'complete';
    const run = spawnSync(PYTHON, ['realign_song.py', key, '--force-confirmed', ...(written ? ['--anchor-embedded'] : [])], { cwd: ROOT, encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8' } });
    const fresh = read(at('aligned.fia'));
    for (const [name, data] of keep) fs.writeFileSync(at(name), data);
    if (!keep.some(([name]) => name === 'timing-edits.json')) fs.rmSync(at('timing-edits.json'), { force: true });
    if (fresh.lyrics.lines.length !== old.lyrics.lines.length) throw new Error(key + ': re-alignment failed ' + (run.stdout || '').slice(-200));
    const edits = fs.existsSync(at('timing-edits.json')) ? read(at('timing-edits.json')) : null;
    for (const index of replaced) {
      const line = fresh.lyrics.lines[index], before = old.lyrics.lines[index], next = old.lyrics.lines[index + 1];
      const limit = next && next.startTime > before.startTime ? next.startTime : Infinity;
      // keep the line's start; if the new words run past the next line, squeeze them evenly rather than cutting them off
      const scale = line.endTime - line.startTime > limit - before.startTime ? (limit - before.startTime) / (line.endTime - line.startTime) : 1, origin = line.startTime;
      const move = node => {
        for (const field of ['startTime', 'endTime']) if (typeof node[field] === 'number') node[field] = round(before.startTime + (node[field] - origin) * scale);
        for (const field of ['words', 'syllables', 'ruby']) for (const child of node[field] || []) move(child);
      };
      move(line);
      line.translation = before.translation;
      old.lyrics.lines[index] = line;
      if (edits?.l?.[index]) edits.l[index].t = line.fullText;
      console.log(`${key} line ${index + 1}: ${line.fullText} (${line.startTime}–${line.endTime})`);
    }
    if (edits) write(at('timing-edits.json'), edits);
  }
  const gone = new Set((job.remove || []).map(line => line - 1));
  if (job.removeFrom) for (let index = job.removeFrom - 1; index < source.rows.length; index++) gone.add(index);
  if (gone.size) {
    if (fs.existsSync(at('timing-edits.json'))) {
      const edits = read(at('timing-edits.json')), moved = {};
      for (const [index, edit] of Object.entries(edits.l)) if (!gone.has(Number(index))) moved[Number(index) - [...gone].filter(i => i < Number(index)).length] = edit;
      edits.l = moved; write(at('timing-edits.json'), edits);
    }
    source.rows = source.rows.filter((_, index) => !gone.has(index));
    old.lyrics.lines = old.lyrics.lines.filter((_, index) => !gone.has(index));
    console.log(`${key}: removed ${gone.size} lines, ${source.rows.length} left`);
  }
  write(at('source.json'), source); write(at('aligned.fia'), old);
  fs.rmSync(at('line-notes.json'), { force: true });
}
