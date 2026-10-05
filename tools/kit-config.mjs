// Where everything lives on this machine. Read from lyric-kit.config.json in the repository
// root (or the file named by LYRIC_KIT_CONFIG); see lyric-kit.config.example.json.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const ROOT = path.dirname(fileURLToPath(import.meta.url));          // tools/
const REPO = path.dirname(ROOT);
const file = process.env.LYRIC_KIT_CONFIG || path.join(REPO, 'lyric-kit.config.json');
if (!fs.existsSync(file)) throw new Error(`No configuration: copy lyric-kit.config.example.json to ${file} and set musicDir`);
const config = JSON.parse(fs.readFileSync(file, 'utf8'));
if (!config.musicDir) throw new Error(`${file}: musicDir is required`);
const at = (value, fallback) => path.resolve(path.dirname(file), value || fallback);

export const MUSIC = at(config.musicDir);
const DATA = at(config.dataDir, path.join(REPO, 'data'));
export const BATCH = at(config.songsDir, path.join(DATA, 'songs'));       // one folder per song
export const BACKUPS = at(config.backupDir, path.join(DATA, 'backups'));  // untouched copies of every MP3 before it is written
export const VIDEO = at(config.videoDir, path.join(DATA, 'video'));       // karaoke video frames and readings read off them
export const CACHE = at(config.cacheDir, path.join(DATA, 'cache'));       // downloaded lyric pages
export const MODELS = at(config.modelDir, path.join(DATA, 'models'));
const windows = process.platform === 'win32';
export const PYTHON = config.python ? at(config.python) : path.join(REPO, '.venv', windows ? 'Scripts/python.exe' : 'bin/python');
export const YUTTO = config.yutto || 'yutto';
export const PROXY = config.proxy || '';
export const WEBBRIDGE = config.webBridge || 'http://127.0.0.1:10086';
export const REVIEW_PORT = Number(config.reviewPort || 3417);
for (const dir of [BATCH, BACKUPS, VIDEO, CACHE]) fs.mkdirSync(dir, { recursive: true });
