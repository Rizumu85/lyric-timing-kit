// Remove lyric rows that are staff credits or singer labels (长笛：xxx, 歌手甲/歌手乙：),
// which are never sung and push the alignment of the lines around them off.
// Prints every row it would remove; pass --write to change source.json and list the keys in
// readings-changed.txt for realign_song.py.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { BATCH } from './kit-config.mjs';

const write = process.argv.includes('--write');
// "label：name" with a short label and no sentence of kana in it, or a bare "names：" label.
const credit = text => /^[^：:]{1,24}\s*[：:]\s*[^：:]{0,40}$/.test(text) && !/[ぁ-ヿ]{3,}/.test(text) && !/[，。！？,!?]/.test(text);
const changed = [];
let total = 0;
for (const key of fs.readdirSync(BATCH)) {
  const file = path.join(BATCH, key, 'source.json');
  if (!fs.existsSync(file) || key.endsWith('-rf')) continue;
  const source = JSON.parse(fs.readFileSync(file, 'utf8'));
  if (!Array.isArray(source.rows)) continue;
  const drop = source.rows.filter(row => credit(row.original.trim()));
  if (!drop.length) continue;
  total += drop.length;
  console.log(source.name);
  for (const row of drop) console.log('   ' + row.original);
  if (write) {
    source.rows = source.rows.filter(row => !drop.includes(row));
    fs.writeFileSync(file, JSON.stringify(source, null, 2), 'utf8');
    changed.push(key);
  }
}
if (write) fs.writeFileSync(path.join(BATCH, 'readings-changed.txt'), changed.join('\n') + '\n', 'utf8');
console.log(`${total} rows in ${changed.length || '?'} songs${write ? ' removed' : ' would be removed'}`);
