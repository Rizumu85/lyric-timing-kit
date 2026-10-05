// Build the listening page from the current drafts of every unfinished song.
// Usage: node build_review.mjs
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { addHangulRuby } from './hangul_ruby.mjs';
import { BATCH, VIDEO } from './kit-config.mjs';

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const FINISHED = new Set(['complete', 'already_complete']);
// Written after the user listened to them; nothing left to review.
const CONFIRMED = new Set();
const TAGS = [
  [/8秒以上/, '大偏移'],
  [/倒序或重合/, '句首倒序'],
  [/声学分数较低|较低声学分数/, '低置信'],
  [/疑似残留中文译文/, '译文混入'],
  [/不足80ms/, '文字错配'],
  [/窗口边缘/, '整体偏移'],
];
const BOILERPLATE = /holdEmbedding|使用原句时间窗|^建议重点试听/;

const readJson = async file => { try { return JSON.parse(await fs.readFile(file, 'utf8')); } catch { return null; } };
const round = value => Math.round(value * 100) / 100;

// [text, start, end] or [text, start, end, [[base, reading], ...]] when the word carries ruby.
function packWord(word) {
  const packed = [word.text, round(word.startTime), round(word.endTime)];
  const reading = part => (part.ruby || []).map(item => item.text).join('');
  if (word.syllables?.some(part => part.ruby?.length)) {
    const body = word.syllables.map(part => part.text).join('');
    const at = word.text.indexOf(body);
    const parts = word.syllables.map(part => [part.text, reading(part)]);
    if (at > 0) parts.unshift([word.text.slice(0, at), '']);
    if (at >= 0 && at + body.length < word.text.length) parts.push([word.text.slice(at + body.length), '']);
    packed.push(parts);
  } else if (word.ruby?.length) {
    packed.push([[word.text, reading(word)]]);
  }
  return packed;
}

function fromFia(doc, source) {
  const rows = source.rows?.length === doc.lyrics.lines.length ? source.rows : [];
  return doc.lyrics.lines.map((line, index) => ({
    s: round(line.startTime),
    e: round(line.endTime),
    t: line.fullText,
    tr: line.translation && line.translation !== line.fullText ? line.translation : '',
    old: typeof rows[index]?.sourceStart === 'number' ? round(rows[index].sourceStart) : undefined,
    w: (line.words || []).map(packWord),
  }));
}

function fromQwen(draft) {
  return draft.groups.flatMap(group => group.lines).map(line => ({
    s: round(line.start),
    e: round(line.end),
    t: line.text,
    tr: '',
    w: line.tokens.map(token => [token.text, round(token.start), round(token.end)]),
  }));
}

// The old timing is often right apart from one constant shift (the lyric sheet was timed to
// another upload of the same recording). When most lines follow such a shift, hearing one line
// confirms it, and only the lines that break the pattern need a look: those get `dev`, how far
// they sit from where the pattern puts them.
function offsetPattern(song) {
  const offsets = song.lines.map(line => line.old == null ? null : line.s - line.old), known = offsets.filter(value => value != null);
  if (known.length < 8) return;
  const median = values => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)];
  const shift = median(known), spread = median(known.map(value => Math.abs(value - shift)));
  if (spread > .35) return; // the lines do not move together: no pattern to speak of
  const limit = Math.min(1, Math.max(.4, spread * 6));
  song.shift = round(shift);
  song.lines.forEach((line, index) => {
    if (offsets[index] == null) return;
    line.exp = round(line.old + shift);
    if (Math.abs(offsets[index] - shift) > limit) line.dev = round(offsets[index] - shift);
  });
}

function applyNotes(song, notes) {
  song.priority = !!notes.priority;
  song.note = notes.note || '';
  // "moved": the lines that sit more than a second away from where the MP3 has them now.
  if (notes.focus === 'moved') {
    song.focus = song.lines.map((line, index) => ({ line, index })).filter(({ line }) => line.old != null && Math.abs(line.old - line.s) > 1)
      .sort((a, b) => Math.abs(b.line.old - b.line.s) - Math.abs(a.line.old - a.line.s)).slice(0, 8).sort((a, b) => a.index - b.index)
      .map(({ line, index }) => ({ s: Math.max(0, Math.min(line.s, line.old) - 2), e: Math.max(line.s, line.old) + 5, label: `第 ${index + 1} 行` }));
    return;
  }
  song.focus = (notes.focus || []).flatMap(spot => {
    if (!spot.match) return [{ s: spot.start, e: spot.end, label: spot.label }];
    return song.lines.filter(line => line.t.includes(spot.match))
      .map(line => ({ s: Math.max(0, line.s - (spot.before ?? 2)), e: line.e + (spot.after ?? 2), label: spot.label }));
  });
  for (const flag of notes.flags || []) {
    const targets = flag.line ? [song.lines[flag.line - 1]] : song.lines.filter(line => line.t.includes(flag.match));
    for (const line of targets) if (line) line.flag = flag.text;
  }
}

export { ROOT, BATCH };

export async function build() {
const notes = (await readJson(path.join(BATCH, 'review-notes.json'))) || {};
// Which lines of each song had their readings compared with a source (UtaTen, Moegirl, a karaoke
// video or a romaji page). A source that matched only part of the lyrics says nothing about the
// other lines, so a song is "checked" only when every line with kanji was compared.
const readings = new Map(), compared = new Map();
for (const file of ['furigana-audit.json', 'moegirl-audit.json', 'video-audit.json'])
  for (const entry of (await readJson(path.join(BATCH, file))) || []) {
    if (!['checked', 'partial'].includes(entry.status)) { if (!readings.has(entry.key)) readings.set(entry.key, entry.status); continue; }
    readings.set(entry.key, 'partial');
    const rows = compared.get(entry.key) || new Set();
    for (const line of entry.rows || []) rows.add(line - 1);
    compared.set(entry.key, rows);
  }
// The lines with kanji whose readings nothing vouches for: not compared with a source, and not
// spelled out in the lyrics themselves, 右手(migite).
const unverified = (key, source) => {
  const RUN = /[㐀-鿿々]+(?:[(（][ぁ-ヿa-zA-Z]+[)）])?/g;
  const runs = index => source.rows[index].original.match(RUN) || [];
  // a word whose reading was compared in one line is taken as read the same way in the others
  const known = new Set([...(compared.get(key) || [])].flatMap(index => source.rows[index] ? runs(index) : []));
  return (source.rows || []).map((row, index) => index).filter(index =>
    !compared.get(key)?.has(index) && runs(index).some(run => !/[(（]/.test(run) && !known.has(run)));
};
// Songs flagged from the Folia player ("something is off"): back on the page, whatever their state.
const flagged = new Map();
for (const flag of (await readJson(path.join(BATCH, 'flagged-songs.json'))) || []) flagged.set(flag.key, [...(flagged.get(flag.key) || []), flag]);
const songs = [];
for (const entry of await fs.readdir(BATCH, { withFileTypes: true })) {
  if (!entry.isDirectory()) continue;
  const folder = path.join(BATCH, entry.name);
  const source = await readJson(path.join(folder, 'source.json'));
  if (!source) continue;
  const result = await readJson(path.join(folder, 'result.json'));
  // Finished songs stay off the page, except Japanese ones whose readings had no source to check against.
  // Lyrics that spell out their own readings, 右手(migite), are their own reading source.
  const kanjiRuns = source.rows?.flatMap(row => row.original.match(/[㐀-鿿々]+(?:[(（][ぁ-ヿa-zA-Z]+[)）])?/g) || []) || [];
  if (kanjiRuns.length && kanjiRuns.filter(run => /[(（]/.test(run)).length >= kanjiRuns.length * .8) readings.set(entry.name, 'checked');
  // Of the lines no source vouches for, keep only those where the audio itself sounds like another
  // reading (reading_check.py); the rest are dictionary readings nothing speaks against.
  const doubts = new Map();
  for (const finding of (await readJson(path.join(folder, 'reading-check.json'))) || []) {
    const now = finding.current || finding.default;
    if (finding.gain >= 0.04 && finding.heard !== now && source.rows[finding.line - 1]?.original.includes(finding.base))
      doubts.set(finding.line - 1, [...(doubts.get(finding.line - 1) || []), `「${finding.base}」现在读 ${now}，听起来更像 ${finding.heard}`]);
  }
  // ... or where what is sung over the word does not sound like its marked reading at all (reading_outliers.py)
  for (const finding of (await readJson(path.join(folder, 'reading-outliers.json'))) || [])
    if (source.rows[finding.line - 1]?.original.includes(finding.base))
      doubts.set(finding.line - 1, [...(doubts.get(finding.line - 1) || []), `「${finding.base}」标的是 ${finding.reading}，但这一句的录音里听不出这个读法`]);
  const loose = source.language === 'ja' && readings.get(entry.name) === 'partial' ? unverified(entry.name, source).filter(index => doubts.has(index)) : [];
  if (readings.get(entry.name) === 'partial' && !loose.length) readings.set(entry.name, 'checked');
  const written = !!result && FINISHED.has(result.status);
  // "recheck" in review-notes.json brings a finished song back for another listen (a reading in doubt).
  if (written && !notes[entry.name]?.recheck && !flagged.has(entry.name) && (source.language !== 'ja' || readings.get(entry.name) === 'checked' || result.confirmed || CONFIRMED.has(entry.name))) continue;
  if (!result && FINISHED.has(source.status)) continue;

  const fia = await readJson(path.join(folder, 'aligned.fia'));
  const qwen = fia ? null : await readJson(path.join(folder, 'qwen3-forced-align-groups.json'));
  if (!fia && !qwen) continue;

  const issues = [...new Set(result?.issues || source.issues || [])].filter(issue => !BOILERPLATE.test(issue));
  const song = {
    key: entry.name,
    file: source.name,
    audio: source.audio,
    title: source.title || path.parse(source.name).name,
    artist: source.artist || '',
    lang: source.language,
    duration: source.duration,
    kind: fia ? 'aligned' : 'dictated',
    written,
    score: result?.meanScore != null ? round(result.meanScore) : null,
    tags: [
      ...(result?.wasStatus === 'complete' ? ['MP3 里是旧版'] : []),
      ...(fia && source.language === 'ja' && readings.get(entry.name) !== 'checked'
        ? [readings.get(entry.name) === 'partial' ? `读音有 ${loose.length} 行可疑` : '读音未核对'] : []),
      ...TAGS.filter(([pattern]) => issues.some(issue => pattern.test(issue))).map(([, tag]) => tag),
    ],
    issues,
    lines: fia ? (addHangulRuby(fia), fromFia(fia, source)) : fromQwen(qwen),
  };
  // A song whose MP3 still carries the previous version: compare against what is embedded now
  // (kept in aligned.bilingual.lrc, with its 50 ms lead) rather than the original lyric sheet.
  if (result?.wasStatus === 'complete') {
    let lrc = '';
    try { lrc = await fs.readFile(path.join(folder, 'aligned.bilingual.lrc'), 'utf8'); } catch {}
    const starts = [...new Set([...lrc.matchAll(/^\[(\d+):(\d+)\.(\d+)\]/gm)].map(m => round(Number(m[1]) * 60 + Number(m[2]) + Number(m[3]) / 100 + 0.05)))];
    if (starts.length === song.lines.length) song.lines.forEach((line, index) => { line.old = starts[index]; });
  }
  offsetPattern(song);
  // mark the lines nothing vouches for, so only those need a look
  if (fia && loose.length && song.lines.length === source.rows.length) for (const index of loose) song.lines[index].nr = doubts.get(index).join('；');
  applyNotes(song, notes[entry.name] || {});
  if (flagged.has(entry.name)) {
    const spots = flagged.get(entry.name).filter(flag => flag.position != null);
    song.priority = true;
    song.tags.unshift('播放时标记');
    song.note = ['你在 Folia 里听这首时点了“歌词有问题”' + (spots.length ? '，当时播放到下面这些位置' : '') + '。找到问题后用「＋ 备注」写一下，或直接调轴；处理完点通过。', song.note].filter(Boolean).join(' ');
    song.focus = [...spots.map(flag => ({ s: Math.max(0, flag.position - 12), e: flag.position + 3, label: '标记处 ' + Math.floor(flag.position / 60) + ':' + String(Math.floor(flag.position % 60)).padStart(2, '0') })), ...(song.focus || [])];
  }
  // Line starts read off a karaoke video of the same recording (video_timing.py). Only the lines
  // where the video disagrees by half a second or more are offered for comparison.
  const video = await readJson(path.join(VIDEO, entry.name, 'video-timing.json'));
  if (video?.report?.length === song.lines.length) {
    let seen = 0, differ = 0;
    video.report.forEach(([, , time], index) => {
      if (time == null) return;
      seen++;
      if (Math.abs(time - song.lines[index].s) >= .5) { song.lines[index].vid = round(time); differ++; }
    });
    song.tags.unshift(`视频对照：${differ} 行不同`);
    song.videoChecked = { seen, differ };
  }
  song.edits = (await readJson(path.join(folder, 'timing-edits.json'))) || { g: 0, l: {} };
  // the user's per-line notes; a note stays with its line only while the line's text is unchanged
  song.notes = Object.fromEntries(Object.entries((await readJson(path.join(folder, 'line-notes.json'))) || {}).filter(([index, note]) => song.lines[index]?.t === note.t));
  // A second translation to compare styles with (translation-alt.json: label, lines, notes by line number).
  const alt = await readJson(path.join(folder, 'translation-alt.json'));
  if (alt?.lines?.length === song.lines.length) song.lines.forEach((line, index) => { line.alt = [alt.label, alt.lines[index], alt.notes?.[index + 1] || '']; });
  // Notes from the acoustic reading check (apply_reading_check.mjs).
  for (const flag of (await readJson(path.join(folder, 'reading-flags.json'))) || []) {
    const line = song.lines[flag.line - 1];
    if (line && line.t === flag.text) line.flag = line.flag ? line.flag + '；' + flag.flag : flag.flag;
  }
  const acoustic = song.lines.filter(line => line.flag?.includes('声学判断') || line.flag?.includes('读音可疑')).length;
  if (acoustic) song.tags.unshift(`声学比对：${acoustic} 处`);
  songs.push(song);
}

const order = Object.keys(notes);
// Separation-model experiment: put each "-rf" draft next to its original at the top, with the
// lines that ended up more than a second apart as spots to compare.
for (const twin of songs.filter(song => song.key.endsWith('-rf'))) {
  const original = songs.find(song => song.key === twin.key.slice(0, -3));
  if (!original) continue;
  const spots = pick => original.lines.map((line, index) => [line, twin.lines[index], index])
    .filter(([a, b]) => b && Math.abs(a.s - b.s) > 1)
    .map(([a, b, index]) => { const line = pick === 'twin' ? b : a; return { s: Math.max(0, line.s - 2), e: Math.min(line.e, line.s + 6) + 1, label: `第 ${index + 1} 行` }; });
  Object.assign(original, { priority: true, focus: spots('original'), note: `对比试验：这是现在的版本（旧的人声分离）。下面是两个版本相差 1 秒以上的句子，和列表里紧挨着的「${twin.title}」对比着听，哪个版本的句首和逐字高亮更准。` });
  Object.assign(twin, { priority: true, focus: spots('twin'), tags: ['新分离模型'], note: '对比试验：同一份歌词，换用新的人声分离模型（BS-RoFormer）后重新打轴的版本。只是草稿，不会写入 MP3。' });
  order.push(original.key, twin.key);
}
songs.sort((a, b) => (b.priority - a.priority)
  || (a.priority ? order.indexOf(a.key) - order.indexOf(b.key) : a.file.localeCompare(b.file, 'zh')));

const onPage = new Set(songs.map(song => song.key));
// the songs that still have lines with unverified readings, for reading_check.py --keys-file
await fs.writeFile(path.join(BATCH, 'unverified-keys.txt'), songs.filter(song => song.lines.some(line => line.nr)).map(song => song.key).join(' '), 'utf8');
// A song the user approved stays approved only while its draft is the one they heard: a draft
// changed afterwards loses the mark and says so.
const approvedAt = (await readJson(path.join(BATCH, 'heard-times.json'))) || {};
const heard = [], stale = [];
for (const key of ((await readJson(path.join(BATCH, 'heard-songs.json'))) || []).filter(key => onPage.has(key))) {
  const changed = (await fs.stat(path.join(BATCH, key, 'aligned.fia')).catch(() => null))?.mtimeMs || 0;
  if (changed <= (approvedAt[key] ?? Infinity)) heard.push(key);
  else { songs.find(song => song.key === key).tags.unshift('有改动，待重听'); stale.push(key); }
}
// Forget the outdated approvals, so that approving the song again counts from that moment.
if (stale.length) {
  const all = (await readJson(path.join(BATCH, 'heard-songs.json'))) || [];
  await fs.writeFile(path.join(BATCH, 'heard-songs.json'), JSON.stringify(all.filter(key => !stale.includes(key)), null, 2), 'utf8');
  await fs.writeFile(path.join(BATCH, 'heard-times.json'), JSON.stringify(Object.fromEntries(Object.entries(approvedAt).filter(([key]) => !stale.includes(key))), null, 2), 'utf8');
}
const data = JSON.stringify({ built: new Date().toISOString(), heard, songs }).replace(/</g, '\\u003c');
const template = await fs.readFile(path.join(ROOT, 'review.template.html'), 'utf8');
const html = template.replace('__DATA__', () => data);
await fs.writeFile(path.join(BATCH, 'review.html'), html, 'utf8');
console.log(`review.html: ${songs.length} songs (${songs.filter(song => song.priority).length} priority), ${Math.round(data.length / 1024)} KB of data`);
return html;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await build();
