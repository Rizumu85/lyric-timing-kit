"""Apply the title translations chosen on the picker page: append " [translation]" to the file
name and to the title tag, and move everything the batch keeps under the song's key (the key is
derived from the file path, so a rename changes it).

Writes title-migration.json (old name, new name, old key, new key) so it can be undone.
Usage: python apply_title_choices.py [--write]     (without --write: dry run)
"""
import hashlib
import json
import sys
from pathlib import Path

from mutagen.id3 import TIT2
from mutagen.mp3 import MP3

from batch_inventory import MUSIC, OUT
from kit_config import BACKUPS, VIDEO
from embed_tags import AUDIO_EXTENSIONS, Song

write = '--write' in sys.argv
key_of = lambda path: hashlib.sha256(str(path).encode()).hexdigest()[:12]
# Titles decided in an earlier round (applied or declined) stay out of the picker and out of this run.
decided_file = OUT / 'title-decided.json'
decided = json.loads(decided_file.read_text(encoding='utf-8')) if decided_file.exists() else []
shown = [row for row in json.loads((OUT / 'title-choices.json').read_text(encoding='utf-8')) if row['file'] not in decided]
choices = [row for row in shown if row['on'] and row['text'].strip()]

moves = []
for row in choices:
    old = next((file for file in (MUSIC / (row['file'] + extension) for extension in AUDIO_EXTENSIONS) if file.exists()), None)
    if old is None:
        print('missing, skipped:', row['file'])
        continue
    text = row['text'].strip()
    new = MUSIC / f"{row['file'].rstrip()} [{text}]{old.suffix}"
    if any(char in text for char in '\\/:*?"<>|'):
        print('not a valid file name, skipped:', text)
        continue
    moves.append({'old': str(old), 'new': str(new), 'oldKey': key_of(old), 'newKey': key_of(new), 'text': text})
print(f'{len(moves)} songs to rename')
if not write:
    for move in moves[:5]:
        print('  ', Path(move['new']).name)
    sys.exit()

keys = {move['oldKey']: move for move in moves}
for move in moves:
    old, new = Path(move['old']), Path(move['new'])
    if old.suffix.lower() == '.mp3':
        audio = MP3(old)
        tags = audio.tags
        if tags is not None and 'TIT2' in tags and '[' not in str(tags['TIT2']):
            tags.setall('TIT2', [TIT2(encoding=tags['TIT2'].encoding, text=[f"{tags['TIT2']} [{move['text']}]"])])
            audio.save(v2_version=tags.version[1] if tags.version[1] in (3, 4) else 4)
    else:
        song = Song(old)
        title = song.fields()['title']
        if title and '[' not in title:
            song.set_title(f"{title} [{move['text']}]")
            song.save()
    old.rename(new)
    for base in (OUT, BACKUPS, VIDEO):
        if (base / move['oldKey']).exists():
            (base / move['oldKey']).rename(base / move['newKey'])
    folder = OUT / move['newKey']
    for name in ('source.json', 'result.json', 'embedding-validation.json'):
        file = folder / name
        if not file.exists():
            continue
        data = json.loads(file.read_text(encoding='utf-8'))
        if 'key' in data: data['key'] = move['newKey']
        if 'name' in data: data['name'] = new.name
        if 'audio' in data: data['audio'] = str(new)
        if 'audioPath' in data: data['audioPath'] = str(new)
        if name == 'source.json' and data.get('title') and '[' not in data['title']: data['title'] = f"{data['title']} [{move['text']}]"
        for field in ('fia', 'backup', 'backupPath', 'sourceFia'):
            if isinstance(data.get(field), str): data[field] = data[field].replace(move['oldKey'], move['newKey'])
        file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

# batch-level records that refer to songs by key or name
for name in ('inventory.json', 'furigana-audit.json', 'moegirl-audit.json', 'video-audit.json'):
    file = OUT / name
    if not file.exists():
        continue
    data = json.loads(file.read_text(encoding='utf-8'))
    for entry in data:
        move = keys.get(entry.get('key'))
        if not move:
            continue
        entry['key'] = move['newKey']
        if 'name' in entry: entry['name'] = Path(move['new']).name
        if 'audio' in entry: entry['audio'] = move['new']
    file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
notes_file = OUT / 'review-notes.json'
notes = json.loads(notes_file.read_text(encoding='utf-8'))
notes_file.write_text(json.dumps({keys[k]['newKey'] if k in keys else k: v for k, v in notes.items()}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
log = OUT / 'title-migration.json'
previous = json.loads(log.read_text(encoding='utf-8')) if log.exists() else []
log.write_text(json.dumps(previous + moves, ensure_ascii=False, indent=2), encoding='utf-8')
decided_file.write_text(json.dumps(decided + [row['file'] for row in shown], ensure_ascii=False, indent=2), encoding='utf-8')
print(f'renamed {len(moves)} songs')
