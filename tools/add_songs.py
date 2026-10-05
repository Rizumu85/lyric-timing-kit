"""Register music files that were added to the Music folder after the batch was inventoried.

batch_inventory.py would re-read every unfinished song from its MP3 and throw away the lyric
corrections made since, so this only creates the folder and inventory entry for files that are
not registered yet. Prints the new keys and writes them to new-keys.txt.
Every format Folia plays is registered (see embed_tags.py for how each keeps its tags). Files
other than MP3 are registered with holdEmbedding: they are written only after a review.
Usage: python add_songs.py
"""
import hashlib
import json

from mutagen.mp3 import MP3

from batch_inventory import MUSIC, OUT, prepare
from embed_tags import AUDIO_EXTENSIONS, Song

inventory_file = OUT / 'inventory.json'
inventory = json.loads(inventory_file.read_text(encoding='utf-8'))
known = {item['key'] for item in inventory}
added = []
for path in sorted(file for file in MUSIC.iterdir() if file.suffix.lower() in AUDIO_EXTENSIONS):
    key = hashlib.sha256(str(path).encode()).hexdigest()[:12]
    if key in known or (OUT / key / 'source.json').exists():
        continue
    item = {'key': key, 'audio': str(path), 'name': path.name, 'status': 'pending', 'issues': []}
    if path.suffix.lower() == '.mp3':
        audio = MP3(path)
        tags = audio.tags
        item['title'] = str(tags.get('TIT2', '')) if tags else ''
        item['artist'] = str(tags.get('TPE1', '')) if tags else ''
        lyrics = tags.getall('USLT') if tags else []
        item['lyricVersions'] = [{'language': t.lang, 'descriptor': t.desc, 'characters': len(t.text)} for t in lyrics]
        plain = [t for t in lyrics if '@Ruby' not in t.text]
        chosen = next((t for t in plain if not t.desc), None) or next(iter(plain), None)
        text = chosen.text if chosen else ''
        item['duration'] = audio.info.length
    else:
        try:
            fields = Song(path).fields()
        except SystemExit as problem:
            print('skipped:', problem)
            continue
        item['title'], item['artist'], text = fields['title'], fields['artist'], fields['lyrics']
        item['lyricVersions'] = [{'language': '', 'descriptor': '', 'characters': len(text)}] if text else []
        item['holdEmbedding'] = True
        item['duration'] = fields['duration']
    item['source'] = 'embedded' if text else None
    if text:
        info = prepare(text, path.name)
        item.update({k: v for k, v in info.items() if k != 'issues'})
        item['issues'].extend(info['issues'])
        if not info['rows']:
            item['status'] = 'needs_lyrics'
    else:
        item.update(status='needs_lyrics', language='unknown', rows=[])
        item['issues'].append('没有内嵌歌词，请提供演唱原文')
    folder = OUT / key
    folder.mkdir(exist_ok=True)
    (folder / 'original-lyrics.txt').write_text(text, encoding='utf-8')
    (folder / 'source.json').write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding='utf-8')
    inventory.append(item)
    added.append(key)
    print(f"{key} {item['status']:<12} {item.get('language', '?'):<7} rows {len(item.get('rows', [])):>3}  {path.name}")
inventory_file.write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding='utf-8')
(OUT / 'new-keys.txt').write_text('\n'.join(added) + '\n', encoding='utf-8')
print(f'{len(added)} songs added')
