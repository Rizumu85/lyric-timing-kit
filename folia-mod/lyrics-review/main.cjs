'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { spawn, exec } = require('node:child_process');

// Where the lyric review page lives (lyric-timing-kit: tools/review_server.mjs). The kit's
// install_folia_mod.mjs writes kit.json next to this file; LYRIC_KIT_TOOLS overrides the folder.
let kit = {};
try { kit = JSON.parse(fs.readFileSync(path.join(__dirname, 'kit.json'), 'utf8')); } catch { /* not installed by the kit's installer */ }
const REVIEW_TOOL = process.env.LYRIC_KIT_TOOLS || kit.tools || '';
const NODE = kit.node || 'node';
const PORT = Number(kit.port) || 3417;
const REVIEW_URL = 'http://127.0.0.1:' + PORT;
// every local format the kit handles
const AUDIO = /\.(mp3|flac|m4a|wav|ogg|opus|aac|alac|ape|wv|tta|wma|aif|aiff)$/i;
const reviewUp = async () => {
  try { return (await (await fetch(REVIEW_URL + '/ping', { signal: AbortSignal.timeout(800) })).text()) === 'lyrics-review'; } catch { return false; }
};
const openInBrowser = url => exec(process.platform === 'win32' ? `start "" "${url}"` : process.platform === 'darwin' ? `open "${url}"` : `xdg-open "${url}"`);

module.exports = function activate(api) {
  // "Something is off with this song": record it on the review page and open that page on the song.
  api.rpc.handle('flagForReview', async ({ fileName, position }) => {
    if (typeof fileName !== 'string' || !AUDIO.test(fileName)) return { ok: false, message: '只有本地音乐文件可以送去复查' };
    if (!await reviewUp()) {
      if (!REVIEW_TOOL) return { ok: false, message: '复查页面没有在运行，也不知道打轴工具装在哪里。请先在工具目录里运行 node install_folia_mod.mjs --write，或者手动打开试听页' };
      // start the review server from the tool's own folder, then wait for it to answer
      const server = spawn(NODE, ['review_server.mjs', String(PORT), '--no-open'], { cwd: REVIEW_TOOL, detached: true, stdio: 'ignore', windowsHide: true });
      server.on('error', () => {}); // reported below as "did not start"
      server.unref();
      for (let attempt = 0; attempt < 20 && !await reviewUp(); attempt++) await new Promise(resolve => setTimeout(resolve, 400));
      if (!await reviewUp()) return { ok: false, message: '复查页面没能启动（' + REVIEW_TOOL + '）' };
    }
    const response = await fetch(REVIEW_URL + '/flag', { method: 'PUT', body: JSON.stringify({ file: path.basename(fileName), position }) });
    if (!response.ok) return { ok: false, message: '复查工具里没有这首歌：' + path.basename(fileName) };
    const { key } = await response.json();
    const url = REVIEW_URL + '/#' + key;
    try { await require('electron').shell.openExternal(url); } catch { openInBrowser(url); }
    return { ok: true, message: '已送去复查，并在浏览器里打开了这首歌' };
  });
};
