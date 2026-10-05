// Cut the end off an MP3 without re-encoding, keeping its ID3 tag (lyrics, cover, everything) byte for byte.
// The original goes to backups/audio-originals/ first.
// Usage: node trim_audio.mjs <key> <end seconds>
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { parseId3 } from './format/id3.mjs';
import { BATCH, CACHE } from './kit-config.mjs';

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const [key, end] = process.argv.slice(2);
const folder = path.join(BATCH, key);
const source = JSON.parse(fs.readFileSync(path.join(folder, 'source.json'), 'utf8'));
const original = fs.readFileSync(source.audio);
const backupDir = path.join(CACHE, 'audio-originals');
fs.mkdirSync(backupDir, { recursive: true });
const backup = path.join(backupDir, crypto.createHash('sha256').update(original).digest('hex').slice(0, 16) + ' ' + source.name);
if (!fs.existsSync(backup)) fs.writeFileSync(backup, original);

// ffmpeg copies the MPEG frames up to the cut and writes a fresh length header for them.
const cut = path.join(backupDir, 'cut.tmp.mp3');
const run = spawnSync('ffmpeg', ['-v', 'error', '-y', '-i', source.audio, '-to', String(end), '-map', '0:a', '-c', 'copy', '-map_metadata', '-1', '-id3v2_version', '0', '-write_id3v1', '0', cut], { encoding: 'utf8' });
if (run.status !== 0) throw new Error(run.stderr);
let audio = fs.readFileSync(cut);
fs.rmSync(cut);
if (audio.subarray(0, 3).toString() === 'ID3') audio = audio.subarray(parseId3(audio).totalSize);

// Original tag bytes, untouched, followed by the shortened audio.
const tag = original.subarray(0, parseId3(original).totalSize);
const output = Buffer.concat([tag, audio]);
const temporary = source.audio + '.trim.tmp';
fs.writeFileSync(temporary, output);
fs.renameSync(temporary, source.audio);
const probe = spawnSync('ffprobe', ['-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', source.audio], { encoding: 'utf8' });
source.duration = Number(probe.stdout.trim());
fs.writeFileSync(path.join(folder, 'source.json'), JSON.stringify(source, null, 2), 'utf8');
console.log(JSON.stringify({ name: source.name, before: original.length, after: output.length, duration: source.duration, tagBytesKept: tag.length, backup }));
