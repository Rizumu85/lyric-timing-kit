// Re-align the songs listed in a keys file, each in the right way:
//   - songs whose MP3 already has lyrics: around the embedded line starts (then clamp strays)
//   - everything else (never written, or the line count changed): the normal way
// Songs the user confirmed are skipped by realign_song.py itself.
// Usage: node realign_keys.mjs <keys file>
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { BATCH, PYTHON, ROOT } from './kit-config.mjs';

const keysFile = path.resolve(process.argv[2] || '');
if (!process.argv[2] || !fs.existsSync(keysFile)) { console.log('Usage: node realign_keys.mjs <keys file>'); process.exit(1); }
const python = args => spawnSync(PYTHON, args, { cwd: ROOT, encoding: 'utf8', maxBuffer: 1 << 28, env: { ...process.env, PYTHONIOENCODING: 'utf-8' } });
const records = text => (text || '').split(/\r?\n/).filter(line => line.startsWith('{')).map(line => JSON.parse(line));

const anchored = records(python(['realign_song.py', '--anchor-embedded', '--keys-file', keysFile]).stdout);
const rest = anchored.filter(row => row.status === 'skipped' && row.error !== 'confirmed by the user').map(row => row.key);
console.log(`anchored: ${anchored.filter(row => row.status !== 'skipped').length} | normal: ${rest.length} | confirmed, left alone: ${anchored.filter(row => row.error === 'confirmed by the user').length}`);
spawnSync(process.execPath, ['clamp_anchored.mjs', '--keys-file', keysFile], { cwd: ROOT });
if (rest.length) {
  const restFile = path.join(BATCH, 'realign-rest.txt');
  fs.writeFileSync(restFile, rest.join('\n') + '\n', 'utf8');
  const counts = {};
  for (const row of records(python(['realign_song.py', '--keys-file', restFile]).stdout)) counts[row.status] = (counts[row.status] || 0) + 1;
  console.log(JSON.stringify(counts));
}
