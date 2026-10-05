"""Give a song its lyric sheet from a text or LRC file, through the same preparation as an import.

For a file that came without embedded lyrics, or whose embedded lyrics are the wrong song. The
sheet may be a platform's LRC (timed lines, translations at the same times) or plain text.
The song is left held from writing: align it, check it against a transcription, and let the
user listen before anything goes into the music file.

Usage: python set_lyrics.py <key> <lyrics file> [--source <where it came from>] [--write]
Without --write it only reports what the sheet would become.
"""
import json
import sys
from pathlib import Path

from batch_inventory import OUT, prepare

args = [arg for arg in sys.argv[1:] if arg != '--write']
write = '--write' in sys.argv
origin = None
if '--source' in args:
    at = args.index('--source')
    origin = args[at + 1]
    del args[at:at + 2]
if len(args) != 2:
    sys.exit(__doc__)
key, lyrics_file = args
folder = OUT / key
source = json.loads((folder / 'source.json').read_text(encoding='utf-8'))
text = Path(lyrics_file).read_text(encoding='utf-8-sig')
info = prepare(text, source['name'])
rows = info['rows']
print(f"{source['name']}: {len(rows)} rows, language {info.get('language')}, issues {info['issues']}")
if rows:
    print(f"  first row at {rows[0]['sourceStart']}, last at {rows[-1]['sourceStart']}; {sum(1 for row in rows if row.get('translation'))} with a translation")
if not write:
    sys.exit('dry run; add --write to replace the lyric sheet')
if not rows:
    sys.exit('no usable lyric lines in that file; nothing changed')

source.update({name: value for name, value in info.items() if name != 'issues'})
source['issues'] = info['issues']
source['status'] = 'pending'
source['source'] = origin or 'file:' + Path(lyrics_file).name
source['holdEmbedding'] = True
(folder / 'original-lyrics.txt').write_text(text, encoding='utf-8')
(folder / 'source.json').write_text(json.dumps(source, ensure_ascii=False, indent=2), encoding='utf-8')
inventory_file = OUT / 'inventory.json'
inventory = json.loads(inventory_file.read_text(encoding='utf-8'))
inventory = [source if item['key'] == key else item for item in inventory]
inventory_file.write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding='utf-8')
print('lyric sheet replaced; the song is pending alignment')
