// Write user-confirmed review drafts into their MP3s and mark them complete.
// Usage: node embed_confirmed.mjs <key> [<key> ...]
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { embedSong } from './batch_embed.mjs';
import { applyTimingEdits } from './timing_edits.mjs';
import { addHangulRuby } from './hangul_ruby.mjs';
import { BATCH, BACKUPS } from './kit-config.mjs';

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const LANGUAGES = { ja: 'jpn', en: 'eng', zh: 'zho', ru: 'rus', ko: 'kor' };
const readJson = async file => JSON.parse(await fs.readFile(file, 'utf8'));
const writeJson = (file, value) => fs.writeFile(file, JSON.stringify(value, null, 2) + '\n', 'utf8');

const rerun = process.argv.includes('--rerun');
for (const key of process.argv.slice(2).filter(arg => arg !== '--rerun')) {
  const folder = path.join(BATCH, key);
  const source = await readJson(path.join(folder, 'source.json'));
  const result = await readJson(path.join(folder, 'result.json'));
  // An automatic re-run must never overwrite an MP3 the user confirmed by listening.
  if (rerun && result.confirmed) { console.log(`${source.name}: 用户确认过，跳过自动写回`); continue; }
  // Manual adjustments from the listening page go into the draft first, exactly once.
  const editsFile = path.join(folder, 'timing-edits.json');
  const edits = await readJson(editsFile).catch(() => null);
  if (edits) {
    const doc = await readJson(path.join(folder, 'aligned.fia'));
    const changed = applyTimingEdits(doc, edits);
    await writeJson(path.join(folder, 'aligned.fia'), doc);
    await fs.rename(editsFile, path.join(folder, 'timing-edits.applied.json'));
    console.log(`${source.name}: 套用了 ${changed} 行手动微调`);
  }
  // Korean words get their romanization as ruby, like furigana over kanji.
  {
    const doc = await readJson(path.join(folder, 'aligned.fia'));
    if (addHangulRuby(doc)) await writeJson(path.join(folder, 'aligned.fia'), doc);
  }
  const validation = await embedSong(
    source.audio,
    path.join(folder, 'aligned.fia'),
    path.join(BACKUPS, key),
    LANGUAGES[source.language] ?? 'und',
    50,
  );
  await writeJson(path.join(folder, 'embedding-validation.json'), validation);
  result.status = 'complete';
  // a song flagged from the player is dealt with once it is written again
  {
    const flagsFile = path.join(BATCH, 'flagged-songs.json');
    const flags = await readJson(flagsFile).catch(() => null);
    if (flags?.some(flag => flag.key === key)) await writeJson(flagsFile, flags.filter(flag => flag.key !== key));
  }
  delete result.wasStatus;
  if (!rerun) result.confirmed = true; // the user listened to this one
  result.backup = validation.backupPath;
  result.issues = result.issues.filter(issue => !/holdEmbedding|尚未写回|未写回 MP3/.test(issue)).concat(rerun ? '核对读音后重新对齐并写入 MP3' : '试听确认后写入 MP3');
  await writeJson(path.join(folder, 'result.json'), result);
  console.log(JSON.stringify({ name: source.name, lines: validation.lineCount, ruby: validation.ruby, versions: validation.versions, backup: path.basename(validation.backupPath) }));
}
