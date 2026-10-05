"""Change (or add) the [translation] at the end of one song's file name and title tag, and move
everything the batch keeps under the song's key (the key is derived from the file path).

Usage: python retitle.py "<part of the current file name>" "<new translation>"
Recorded in title-migration.json like the renames made by apply_title_choices.py.
"""
import hashlib
import json
import re
import sys
from pathlib import Path

from mutagen.id3 import TIT2
from mutagen.mp3 import MP3

from batch_inventory import MUSIC, OUT
from kit_config import BACKUPS, VIDEO
from embed_tags import AUDIO_EXTENSIONS, Song

needle, text = sys.argv[1], sys.argv[2].strip()
if any(char in text for char in '\\/:*?"<>|'):
    sys.exit('not a valid file name: ' + text)
matches = [file for file in MUSIC.iterdir() if file.suffix.lower() in AUDIO_EXTENSIONS and needle in file.name]
if len(matches) != 1:
    sys.exit(f'{len(matches)} files match: ' + ', '.join(file.name for file in matches[:5]))
old = matches[0]
bracket = re.compile(r'\s*\[[^\]]*\]\s*$')
retitled = lambda title: bracket.sub('', title).rstrip() + f' [{text}]'
new = old.with_name(retitled(old.stem) + old.suffix)
key_of = lambda path: hashlib.sha256(str(path).encode()).hexdigest()[:12]
old_key, new_key = key_of(old), key_of(new)

if old.suffix.lower() == '.mp3':
    audio = MP3(old)
    tags = audio.tags
    if tags is not None and 'TIT2' in tags:
        tags.setall('TIT2', [TIT2(encoding=tags['TIT2'].encoding, text=[retitled(str(tags['TIT2']))])])
        audio.save(v2_version=tags.version[1] if tags.version[1] in (3, 4) else 4)
    title_now = lambda: MP3(new).tags.get('TIT2')
else:
    song = Song(old)
    if song.fields()['title']:
        song.set_title(retitled(song.fields()['title']))
        song.save()
    title_now = lambda: Song(new).fields()['title']
old.rename(new)
for base in (OUT, BACKUPS, VIDEO):
    if (base / old_key).exists():
        (base / old_key).rename(base / new_key)
for name in ('source.json', 'result.json', 'embedding-validation.json'):
    file = OUT / new_key / name
    if not file.exists():
        continue
    data = json.loads(file.read_text(encoding='utf-8'))
    if 'key' in data: data['key'] = new_key
    if 'name' in data: data['name'] = new.name
    if 'audio' in data: data['audio'] = str(new)
    if 'audioPath' in data: data['audioPath'] = str(new)
    if name == 'source.json' and data.get('title'): data['title'] = retitled(data['title'])
    for field in ('fia', 'backup', 'backupPath', 'sourceFia'):
        if isinstance(data.get(field), str): data[field] = data[field].replace(old_key, new_key)
    file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
# batch-level records that refer to songs by key or name
for name in ('inventory.json', 'furigana-audit.json', 'moegirl-audit.json', 'video-audit.json'):
    file = OUT / name
    if not file.exists():
        continue
    data = json.loads(file.read_text(encoding='utf-8'))
    for entry in data:
        if entry.get('key') != old_key:
            continue
        entry['key'] = new_key
        if 'name' in entry: entry['name'] = new.name
        if 'audio' in entry: entry['audio'] = str(new)
    file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
notes_file = OUT / 'review-notes.json'
notes = json.loads(notes_file.read_text(encoding='utf-8'))
notes_file.write_text(json.dumps({new_key if key == old_key else key: value for key, value in notes.items()}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
heard_file = OUT / 'heard-songs.json'
if heard_file.exists():
    heard_file.write_text(json.dumps([new_key if key == old_key else key for key in json.loads(heard_file.read_text(encoding='utf-8'))], indent=2) + '\n', encoding='utf-8')
log = OUT / 'title-migration.json'
log.write_text(json.dumps(json.loads(log.read_text(encoding='utf-8')) + [{'old': str(old), 'new': str(new), 'oldKey': old_key, 'newKey': new_key, 'text': text}], ensure_ascii=False, indent=2), encoding='utf-8')
print(f'{old.name} -> {new.name} ({old_key} -> {new_key}); title tag: {title_now()}')
