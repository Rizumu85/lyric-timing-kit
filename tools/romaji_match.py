"""Decide whether a list of Latin-letter lines taken from a web page is the romaji of a song's
lyrics, by comparing it with the song's own kana readings (prepared.json), loosely enough to
survive the usual romanization differences (wo/o, ou/ō, particles written wa/e).

    python romaji_match.py <key> <lines file> [--save]

Prints {"share": fraction of our rows found on the page, "lines": lyric lines kept}. With --save
the kept lines are written to backups/video/<key>/readings.txt for video_audit.py.
"""
import json
import re
import sys
import unicodedata
from pathlib import Path

import romkan

from batch_inventory import OUT
from kit_config import VIDEO

key, lines_file = sys.argv[1], Path(sys.argv[2])


def loose(text):
    text = unicodedata.normalize('NFKD', text.lower())
    text = ''.join(char for char in text if 'a' <= char <= 'z')
    text = text.replace('wo', 'o').replace('wa', 'ha').replace('dzu', 'zu').replace('du', 'zu')
    return re.sub(r'(.)\1+', r'\1', text.replace('ou', 'o'))


def grams(text, size=5):
    return {text[index:index + size] for index in range(len(text) - size + 1)}


rows = []
for row in json.loads((OUT / key / 'prepared.json').read_text(encoding='utf-8'))['rows']:
    kana = re.sub(r'\{[^|{}]*\|([^{}]*)\}', r'\1', row['annotated'])
    kana = ''.join(char for char in kana if 'ぁ' <= char <= 'ヿ')
    text = loose(romkan.to_roma(kana))
    if len(text) >= 8:
        rows.append(text)
lines = [line.strip() for line in lines_file.read_text(encoding='utf-8').splitlines() if line.strip()]
page = grams(''.join(loose(line) for line in lines))
found = [row for row in rows if len(grams(row) & page) >= 0.7 * len(grams(row))]
ours = set().union(*(grams(row) for row in rows)) if rows else set()
kept = [line for line in lines if len(loose(line)) >= 5 and len(grams(loose(line)) & ours) >= 0.5 * len(grams(loose(line)))]
share = len(found) / len(rows) if rows else 0
if '--save' in sys.argv:
    folder = VIDEO / key
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'readings.txt').write_text('\n'.join(kept) + '\n', encoding='utf-8')
print(json.dumps({'share': round(share, 2), 'lines': len(kept)}))
