// Local server for the listening page: always serves a freshly built page, streams the
// MP3s, and saves timing adjustments next to each draft as timing-edits.json.
// Usage: node review_server.mjs [port]
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import crypto from 'node:crypto';
import { exec, execFileSync } from 'node:child_process';
import { build, BATCH } from './build_review.mjs';
import { MUSIC, ROOT, REVIEW_PORT } from './kit-config.mjs';

const PORT = Number(process.argv[2] || REVIEW_PORT);
const KEY = /^[0-9a-f]{12}(-rf)?$/;

// What a browser plays as it is. Anything else Folia plays (APE, WavPack, TTA, WMA, AIFF, ALAC)
// is converted once to FLAC, which is lossless and keeps the timeline, and kept in a temp folder.
const AUDIO = /\.(mp3|flac|m4a|wav|ogg|opus|aac|alac|ape|wv|tta|wma|aif|aiff)$/i;
const TYPES = { '.mp3': 'audio/mpeg', '.flac': 'audio/flac', '.m4a': 'audio/mp4', '.wav': 'audio/wav', '.ogg': 'audio/ogg', '.opus': 'audio/ogg', '.aac': 'audio/aac' };
const served = new Map();
function playable(file) {
  const stat = fs.statSync(file), id = file + '|' + stat.size + '|' + stat.mtimeMs;
  if (served.has(id)) return served.get(id);
  const extension = path.extname(file).toLowerCase();
  let direct = extension in TYPES;
  // an .m4a may hold ALAC, which browsers do not decode
  if (extension === '.m4a') direct = execFileSync('ffprobe', ['-v', 'error', '-select_streams', 'a:0', '-show_entries', 'stream=codec_name', '-of', 'csv=p=0', file], { encoding: 'utf8' }).trim() !== 'alac';
  let result = { file, type: TYPES[extension] };
  if (!direct) {
    const folder = path.join(os.tmpdir(), 'lyric-review-audio'), cache = path.join(folder, crypto.createHash('sha1').update(id).digest('hex') + '.flac');
    if (!fs.existsSync(cache)) {
      fs.mkdirSync(folder, { recursive: true });
      execFileSync('ffmpeg', ['-v', 'error', '-y', '-i', file, '-map', '0:a:0', '-c:a', 'flac', '-f', 'flac', cache + '.part']);
      fs.renameSync(cache + '.part', cache);
    }
    result = { file: cache, type: 'audio/flac' };
  }
  served.set(id, result);
  return result;
}

function sendAudio(request, response, name) {
  const original = path.join(MUSIC, path.basename(name));
  if (!AUDIO.test(original) || !fs.existsSync(original)) { response.writeHead(404); return response.end(); }
  let file, type;
  try { ({ file, type } = playable(original)); } catch { response.writeHead(500); return response.end(); }
  const size = fs.statSync(file).size, range = /bytes=(\d+)-(\d*)/.exec(request.headers.range || '');
  const start = range ? Number(range[1]) : 0, end = range && range[2] ? Number(range[2]) : size - 1;
  response.writeHead(range ? 206 : 200, {
    'Content-Type': type, 'Accept-Ranges': 'bytes', 'Content-Length': end - start + 1,
    ...(range ? { 'Content-Range': `bytes ${start}-${end}/${size}` } : {}),
  });
  // Read and close at once: a stream the browser has paused would keep the MP3 locked,
  // and writing lyrics into it would then fail.
  const handle = fs.openSync(file, 'r'), buffer = Buffer.alloc(end - start + 1);
  try { fs.readSync(handle, buffer, 0, buffer.length, start); } finally { fs.closeSync(handle); }
  response.end(buffer);
}

function saveEdits(request, response, key) {
  const folder = path.join(BATCH, key);
  if (!KEY.test(key) || !fs.existsSync(folder)) { response.writeHead(404); return response.end(); }
  let body = '';
  request.on('data', chunk => { body += chunk; if (body.length > 1e6) request.destroy(); });
  request.on('end', () => {
    try {
      const edits = JSON.parse(body), file = path.join(folder, 'timing-edits.json');
      if (!edits.g && !Object.keys(edits.l || {}).length) fs.rmSync(file, { force: true });
      else fs.writeFileSync(file, JSON.stringify({ g: edits.g || 0, l: edits.l || {} }, null, 2) + '\n', 'utf8');
      response.writeHead(204); response.end();
    } catch (error) { response.writeHead(400); response.end(String(error.message)); }
  });
}

http.createServer(async (request, response) => {
  const url = decodeURIComponent(new URL(request.url, 'http://x').pathname);
  if (request.method === 'GET' && url === '/') {
    response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' });
    // Load the builder afresh when its file has changed, so a running server never serves a
    // page made by an old version of it.
    const builder = new URL('./build_review.mjs', import.meta.url);
    const fresh = await import(builder.href + '?v=' + fs.statSync(builder).mtimeMs);
    return response.end(await fresh.build());
  }
  // Title translation picker: suggestions from title_translations.mjs, the user's choices saved beside them.
  if (url === '/titles') {
    const choices = path.join(BATCH, 'title-choices.json');
    if (request.method === 'PUT') {
      let body = '';
      request.on('data', chunk => { body += chunk; });
      request.on('end', () => {
        try { fs.writeFileSync(choices, JSON.stringify(JSON.parse(body), null, 2) + '\n', 'utf8'); response.writeHead(204); response.end(); }
        catch (error) { response.writeHead(400); response.end(String(error.message)); }
      });
      return;
    }
    const saved = new Map((fs.existsSync(choices) ? JSON.parse(fs.readFileSync(choices, 'utf8')) : []).map(row => [row.file, row]));
    // the foreign-language original of katakana titles, as a second suggestion
    const decidedFile = path.join(BATCH, 'title-decided.json');
    const decided = new Set(fs.existsSync(decidedFile) ? JSON.parse(fs.readFileSync(decidedFile, 'utf8')) : []);
    const originals = JSON.parse(fs.readFileSync(path.join(BATCH, 'title-originals.json'), 'utf8'));
    const rows = JSON.parse(fs.readFileSync(path.join(BATCH, 'title-translations.json'), 'utf8'))
      // only titles still waiting for a decision: not yet applied or declined, and the file still has that name
      .filter(row => !decided.has(row.file) && fs.readdirSync(MUSIC).some(name => AUDIO.test(name) && name.replace(AUDIO, '') === row.file))
      .map(row => ({ file: row.file, found: row.found, chinese: row.suggestion, original: originals[row.file] || '', on: saved.get(row.file)?.on ?? !!row.suggestion, text: saved.get(row.file)?.text ?? row.suggestion }));
    const page = fs.readFileSync(path.join(ROOT, 'titles.template.html'), 'utf8').replace('__DATA__', () => JSON.stringify(rows).replace(/</g, '\\u003c'));
    response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' });
    return response.end(page);
  }
  if (request.method === 'PUT' && url === '/heard') {
    // the keys of the songs marked 已听 on the page
    let body = '';
    request.on('data', chunk => { body += chunk; });
    request.on('end', () => {
      try {
        const keys = JSON.parse(body).filter(key => KEY.test(key));
        // remember when each song was approved: a draft changed after that needs another listen
        const timesFile = path.join(BATCH, 'heard-times.json');
        const old = fs.existsSync(timesFile) ? JSON.parse(fs.readFileSync(timesFile, 'utf8')) : {};
        fs.writeFileSync(timesFile, JSON.stringify(Object.fromEntries(keys.map(key => [key, old[key] || Date.now()])), null, 2) + '\n', 'utf8');
        fs.writeFileSync(path.join(BATCH, 'heard-songs.json'), JSON.stringify(keys, null, 2) + '\n', 'utf8');
        response.writeHead(204); response.end();
      }
      catch (error) { response.writeHead(400); response.end(String(error.message)); }
    });
    return;
  }
  if (request.method === 'GET' && url === '/ping') { response.writeHead(200); return response.end('lyrics-review'); }
  if (request.method === 'PUT' && url === '/flag') {
    // "Something is off with this song", sent by the Folia mod while the song is playing:
    // remember it (flagged-songs.json) so the page brings the song back, and answer with its key.
    let body = '';
    request.on('data', chunk => { body += chunk; });
    request.on('end', () => {
      try {
        const { file, position } = JSON.parse(body);
        const name = path.basename(String(file || '')).normalize('NFC');
        const key = fs.readdirSync(BATCH).find(folder => {
          try { return JSON.parse(fs.readFileSync(path.join(BATCH, folder, 'source.json'), 'utf8')).name.normalize('NFC') === name; } catch { return false; }
        });
        if (!key) { response.writeHead(404); return response.end(JSON.stringify({ error: 'no such song in the batch', file: name })); }
        const flagsFile = path.join(BATCH, 'flagged-songs.json');
        const flags = fs.existsSync(flagsFile) ? JSON.parse(fs.readFileSync(flagsFile, 'utf8')) : [];
        flags.push({ key, file: name, at: Date.now(), position: Number.isFinite(position) ? Math.round(position * 10) / 10 : null });
        fs.writeFileSync(flagsFile, JSON.stringify(flags, null, 2) + '\n', 'utf8');
        response.writeHead(200, { 'Content-Type': 'application/json' }); response.end(JSON.stringify({ key }));
      } catch (error) { response.writeHead(400); response.end(String(error.message)); }
    });
    return;
  }
  if (request.method === 'GET' && url.startsWith('/music/')) return sendAudio(request, response, url.slice(7));
  if (request.method === 'PUT' && url.startsWith('/edits/')) return saveEdits(request, response, url.slice(7));
  if (request.method === 'PUT' && url.startsWith('/notes/')) {
    // per-line notes for Claude, kept beside the draft as line-notes.json
    const key = url.slice(7), folder = path.join(BATCH, key);
    if (!KEY.test(key) || !fs.existsSync(folder)) { response.writeHead(404); return response.end(); }
    let body = '';
    request.on('data', chunk => { body += chunk; });
    request.on('end', () => {
      try {
        const notes = JSON.parse(body), file = path.join(folder, 'line-notes.json');
        if (Object.keys(notes).length) fs.writeFileSync(file, JSON.stringify(notes, null, 2), 'utf8'); else fs.rmSync(file, { force: true });
        response.writeHead(204); response.end();
      } catch (error) { response.writeHead(400); response.end(String(error.message)); }
    });
    return;
  }
  response.writeHead(404); response.end();
}).listen(PORT, '127.0.0.1', () => {
  const address = `http://127.0.0.1:${PORT}/`;
  console.log(`试听页：${address}（关闭这个窗口即停止）`);
  if (!process.argv.includes('--no-open')) exec(`start "" "${address}"`);
});
