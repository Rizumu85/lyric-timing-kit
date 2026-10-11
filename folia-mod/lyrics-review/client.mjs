// Heard something wrong while a song plays: send it to the lyric review page.
// A command for the palette, a button beside the progress bar and a tab in the player panel;
// nothing else is touched.
const label = (zh, en) => ({ 'zh-CN': zh, en: en || zh });

// Folia 0.7.12 keeps a local song's file path in its own database; read-only lookup.
async function localFilePath(song) {
  if (song?.localData?.filePath) return song.localData.filePath;
  const id = song?.localRef?.songId;
  if (!id || typeof indexedDB === 'undefined') return null;
  return new Promise(resolve => {
    const request = indexedDB.open('KineticPlayerDB');
    request.onupgradeneeded = () => { request.transaction.abort(); };
    request.onerror = () => resolve(null);
    request.onsuccess = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains('local_music')) { db.close(); resolve(null); return; }
      const transaction = db.transaction('local_music', 'readonly');
      const record = transaction.objectStore('local_music').get(id);
      record.onsuccess = () => resolve(record.result?.filePath || null);
      record.onerror = () => resolve(null);
      transaction.oncomplete = () => db.close();
    };
  });
}

export default function activate(folium) {
  if (folium.env.context !== 'main') return;

  const flagForReview = async () => {
    const state = folium.internals.stores.playback.getState(), song = state.currentSong;
    if (!song) return '现在没有在播放的歌';
    const filePath = await localFilePath(song);
    if (!filePath) return '只有本地歌曲可以送去复查';
    const position = [state.currentTime, state.position, state.progress].find(value => Number.isFinite(value));
    return (await folium.rpc.call('flagForReview', { fileName: filePath.split(/[\\/]/).pop(), position })).message;
  };

  folium.registries.commands.register({
    id: 'flag-for-review',
    label: label('这首歌词有问题，送去复查', 'Lyrics look wrong: send this song to review'),
    keywords: ['复查', '审查', '歌词', '问题', '打轴', 'review', 'flag', 'lyrics'],
    run: flagForReview,
  });
  // A small flag beside the progress bar. Folia shows it on each of its progress bars, except the
  // collapsed capsule, which is too narrow. One click sends the song and says what happened.
  // It is not drawn for songs that are not local files, which cannot be sent.
  folium.registries.controlButtons?.register({
    id: 'flag',
    slot: 'progress.trailing',
    order: 520,
    hideWhenCollapsed: true,
    mount: container => {
      const style = document.createElement('style');
      style.textContent = `
        button { appearance:none; display:inline-flex; align-items:center; justify-content:center; width:24px; height:24px; padding:0; border:0; border-radius:999px;
          background:transparent; color:var(--folium-progress-text,currentColor); opacity:.6; cursor:pointer; transition:opacity .15s ease-out; }
        button:hover, button:focus-visible { opacity:1; }
        button:focus-visible { outline:1.5px solid currentColor; outline-offset:1px; }
        button:disabled { opacity:.3; cursor:default; }
        button[hidden] { display:none; }`;
      const button = document.createElement('button');
      button.type = 'button';
      button.title = '这首歌词有问题，送去复查';
      button.setAttribute('aria-label', button.title);
      button.innerHTML = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 21V4"/><path d="M5 4h13l-3 4.5 3 4.5H5"/></svg>';
      const click = async () => {
        button.disabled = true;
        let message;
        try { message = await flagForReview(); } catch (error) { message = '没有送成：' + (error?.message || error); }
        button.disabled = false;
        folium.ui?.toast?.(message, { type: 'info', durationMs: 2600 });
      };
      button.addEventListener('click', click);
      const store = folium.internals.stores.playback;
      const sync = () => { const song = store.getState().currentSong; button.hidden = !(song?.localData?.filePath || song?.localRef?.songId); };
      sync();
      const unsubscribe = store.subscribe(sync);
      container.append(style, button);
      return () => { unsubscribe(); button.removeEventListener('click', click); button.remove(); style.remove(); };
    },
  });
  folium.registries.playerPanelTabs.register({
    id: 'review',
    label: label('歌词复查', 'Lyric review'),
    order: 520,
    mount: container => {
      const root = document.createElement('div');
      root.style.cssText = 'display:flex;flex-direction:column;gap:10px;padding:4px 2px;font:inherit;color:inherit';
      root.innerHTML = `
        <h2 style="margin:0;font-size:15px;font-weight:600">歌词复查</h2>
        <p style="margin:0;font-size:13px;opacity:.75">这首的歌词、读音或时间不对？送去试听页，之后在那里标出问题。</p>
        <button type="button" style="align-self:flex-start;appearance:none;cursor:pointer;font:inherit;color:inherit;padding:6px 16px;border:1px solid currentColor;border-radius:999px;background:transparent">送去复查</button>
        <p aria-live="polite" style="margin:0;min-height:1.2em;font-size:12px;opacity:.7"></p>`;
      const button = root.querySelector('button'), result = root.querySelector('p[aria-live]');
      const click = async () => {
        button.disabled = true; button.style.opacity = '.4'; result.textContent = '正在送去复查…';
        try { result.textContent = await flagForReview(); } catch (error) { result.textContent = '没有送成：' + (error?.message || error); }
        button.disabled = false; button.style.opacity = '';
      };
      button.addEventListener('click', click);
      container.append(root);
      return () => { button.removeEventListener('click', click); root.remove(); };
    },
  });
}
