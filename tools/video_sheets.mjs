// Download a karaoke / lyric video at low resolution and lay its frames out as contact sheets,
// so the readings shown on screen can be read off a handful of images.
// One frame every 4 s, 8 frames per sheet (2 x 4), in backups/video/<key>/sheetNN.jpg.
// Usage: node video_sheets.mjs <key> <YouTube id>
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { VIDEO, YUTTO, PROXY } from './kit-config.mjs';

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const [key, id] = process.argv.slice(2);
const folder = path.join(VIDEO, key);
fs.mkdirSync(folder, { recursive: true });
const video = path.join(folder, 'video.mp4');
// A folder left from another video of this song must not be reused: everything in it (the video,
// its sheets, the readings read off them) belongs to that other video.
const idFile = path.join(folder, 'video-id.txt');
if ((fs.existsSync(idFile) ? fs.readFileSync(idFile, 'utf8').trim() : '') !== id) {
  for (const name of fs.readdirSync(folder)) fs.rmSync(path.join(folder, name), { recursive: true, force: true });
  fs.writeFileSync(idFile, id, 'utf8');
}
if (!fs.existsSync(video) && /^BV/.test(id)) {
  // Bilibili: yutto with the saved login, through the local proxy (Bilibili's API rejects this
  // machine's direct address). Run from PowerShell — the proxy port is not reachable from Git Bash.
  const yutto = YUTTO;
  const run = spawnSync(yutto, [`https://www.bilibili.com/video/${id}`, '-q', '16', '--video-only', '-d', folder, '--no-danmaku', '--no-subtitle', ...(PROXY ? ['--proxy', PROXY] : [])], { encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' } });
  const made = fs.readdirSync(folder).filter(file => /\.(mp4|mkv|flv)$/i.test(file) && file !== 'video.mp4');
  if (!made.length) throw new Error('download failed: ' + ((run.stdout || '') + (run.stderr || '')).slice(-300));
  fs.renameSync(path.join(folder, made[0]), video);
}
if (!fs.existsSync(video)) {
  const run = spawnSync('yt-dlp', ['-q', '--no-warnings', '-f', 'bv*[height<=360]/b[height<=360]/18', '--extractor-args', 'youtube:player_client=android', '-o', video, `https://www.youtube.com/watch?v=${id}`], { encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8' } });
  if (!fs.existsSync(video)) throw new Error('download failed: ' + (run.stderr || '').slice(-300));
}
for (const file of fs.readdirSync(folder)) if (/^sheet\d+\.jpg$/.test(file)) fs.rmSync(path.join(folder, file));
const sheets = spawnSync('ffmpeg', ['-v', 'error', '-y', '-i', video, '-vf', 'fps=1/4,scale=640:360:force_original_aspect_ratio=decrease,pad=640:360:(ow-iw)/2:(oh-ih)/2,tile=2x4', '-q:v', '4', path.join(folder, 'sheet%02d.jpg')], { encoding: 'utf8' });
if (sheets.status !== 0) throw new Error(sheets.stderr);
const made = fs.readdirSync(folder).filter(file => /^sheet\d+\.jpg$/.test(file)).sort();
console.log(JSON.stringify({ key, id, folder, sheets: made.length, note: 'frames read left to right, top to bottom, 4 s apart' }));
