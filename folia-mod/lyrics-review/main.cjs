'use strict';

const path = require('node:path');
const { spawn, exec } = require('node:child_process');

// The lyric review page (lyric-timing-kit: tools/review_server.mjs) that a flagged song is sent to.
// Set LYRIC_KIT_TOOLS to the kit's tools/ folder (the one holding review_server.mjs), or edit this line.
const REVIEW_TOOL = process.env.LYRIC_KIT_TOOLS || '';
const REVIEW_URL = 'http://127.0.0.1:3417';
const reviewUp = async () => {
  try { return (await (await fetch(REVIEW_URL + '/ping', { signal: AbortSignal.timeout(800) })).text()) === 'lyrics-review'; } catch { return false; }
};

module.exports = function activate(api) {
  // "Something is off with this song": record it on the review page and open that page on the song.
  api.rpc.handle('flagForReview', async ({ fileName, position }) => {
    if (typeof fileName !== 'string' || !/\.mp3$/i.test(fileName)) return { ok: false, message: '只有本地 MP3 可以送去复查' };
    if (!await reviewUp()) {
      if (!REVIEW_TOOL) return { ok: false, message: '复查页面没有在运行，也没有设置 LYRIC_KIT_TOOLS，无法自动启动' };
      // start the review server from the tool's own folder, then wait for it to answer
      const server = spawn('node', ['review_server.mjs', '3417', '--no-open'], { cwd: REVIEW_TOOL, detached: true, stdio: 'ignore', windowsHide: true });
      server.on('error', () => {}); // reported below as "did not start"
      server.unref();
      for (let attempt = 0; attempt < 20 && !await reviewUp(); attempt++) await new Promise(resolve => setTimeout(resolve, 400));
      if (!await reviewUp()) return { ok: false, message: '复查页面没能启动（' + REVIEW_TOOL + '）' };
    }
    const response = await fetch(REVIEW_URL + '/flag', { method: 'PUT', body: JSON.stringify({ file: path.basename(fileName), position }) });
    if (!response.ok) return { ok: false, message: '复查工具里没有这首歌：' + path.basename(fileName) };
    const { key } = await response.json();
    const url = REVIEW_URL + '/#' + key;
    try { await require('electron').shell.openExternal(url); } catch { exec('start "" "' + url + '"'); }
    return { ok: true, message: '已送去复查，并在浏览器里打开了这首歌' };
  });
};
