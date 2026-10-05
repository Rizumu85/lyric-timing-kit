"""Compare what the recognition model heard (heard.json) with a song's lyric rows.

Reports, per song:
  - lyric rows that were never heard (staff credits, lines from another version, unsung lines)
  - phrases that were heard but match no lyric row (spoken parts, missing lines)
Text is compared by sound where possible: Japanese as kana, Chinese as simplified characters,
other languages as lower-case letters. Writes <key>/lyric-check.json and 歌词核对.md.
Usage: python lyric_check.py [<key> ...]     (no keys = every song with heard.json)
"""
import json
import re
import sys
from difflib import SequenceMatcher

import pykakasi
import romkan
from opencc import OpenCC

from batch_inventory import OUT

kakasi = pykakasi.kakasi()
simplify = OpenCC('t2s')
SPLIT = re.compile(r'[。、，,.!?？！；;：:\s…「」『』（）()~～♪・]+')


def sound(text, language):
    # A reading written into the lyrics, 落下傘（パラシュート）, is what gets sung.
    text = re.sub(r'[㐀-鿿々]+[\(（]([ぁ-ヿa-zA-Z]+)[\)）]', lambda m: romkan.to_hiragana(m[1].lower()) if m[1].isascii() else m[1], text)
    if language == 'ja':
        text = ''.join(item['hira'] for item in kakasi.convert(text))
        text = ''.join(chr(ord(c) - 96) if 'ァ' <= c <= 'ヶ' else c for c in text)
        return re.sub(r'[^ぁ-ゖーa-z0-9一-鿿]', '', text.lower())
    return re.sub(r'[^a-z0-9㐀-鿿а-яё가-힣]', '', simplify.convert(text).lower())


def covered(needle, haystack):
    """Share of needle found, in order, inside haystack."""
    if not needle:
        return 1.0
    blocks = SequenceMatcher(None, needle, haystack, autojunk=False).get_matching_blocks()
    return sum(block.size for block in blocks) / len(needle)


def check(key):
    folder = OUT / key
    source = json.loads((folder / 'source.json').read_text(encoding='utf-8'))
    heard = json.loads((folder / 'heard.json').read_text(encoding='utf-8'))['chunks']
    lines = json.loads((folder / 'aligned.fia').read_text(encoding='utf-8'))['lyrics']['lines'] if (folder / 'aligned.fia').exists() else []
    language = source['language']
    rows = [row['original'] for row in source['rows']]
    row_sounds = [sound(row, language) for row in rows]
    chunk_sounds = [sound(chunk['text'], language) for chunk in heard]
    everything = ''.join(chunk_sounds)

    unheard = []
    for index, (row, voice) in enumerate(zip(rows, row_sounds)):
        if len(voice) < 3:
            continue
        # Prefer the chunks around the line's aligned time; fall back to the whole song.
        near = everything
        if index < len(lines):
            at = lines[index]['startTime']
            near = ''.join(s for chunk, s in zip(heard, chunk_sounds) if chunk['start'] - 12 <= at <= chunk['end'] + 12) or everything
        best = max(covered(voice, near), covered(voice, everything) if near is not everything else 0)
        if best < .5:
            unheard.append({'line': index + 1, 'text': row, 'match': round(best, 2), 'time': round(lines[index]['startTime'], 1) if index < len(lines) else None})

    # Heard but not in the lyrics: stretches of a window that match nothing in the lyric rows
    # sung around that time. Short gaps are mishearings; only long ones are worth a look.
    lyrics_sound = ''.join(row_sounds)
    shortest = 8 if language == 'ja' else 6
    extra, seen = [], []
    for chunk, voice in zip(heard, chunk_sounds):
        if len(lines) == len(rows):
            near = ''.join(s for line, s in zip(lines, row_sounds) if chunk['start'] - 15 <= line['startTime'] <= chunk['end'] + 15) or lyrics_sound
        else:
            near = lyrics_sound
        matched = [False] * len(voice)
        for block in SequenceMatcher(None, voice, near, autojunk=False).get_matching_blocks():
            for i in range(block.a, block.a + block.size):
                matched[i] = block.size >= 2
        # a window's first and last second are often cut mid-word, so ignore gaps touching its edges
        for found in re.finditer('0{%d,}' % shortest, ''.join('1' if m else '0' for m in matched)):
            if found.start() == 0 or found.end() == len(voice):
                continue
            piece = voice[found.start():found.end()]
            if len(set(piece)) < 4 or covered(piece, lyrics_sound) >= .7:
                continue
            if any(covered(piece, old) >= .6 or covered(old, piece) >= .6 for old in seen):
                continue  # the same stretch from the overlapping window
            seen.append(piece)
            extra.append({'heard': piece, 'around': chunk['start']})
    result = {'key': key, 'name': source['name'], 'rows': len(rows), 'unheard': unheard, 'extra': extra}
    (folder / 'lyric-check.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


keys = sys.argv[1:] or sorted(path.parent.name for path in OUT.glob('*/heard.json'))
results = [check(key) for key in keys]
clock = lambda seconds: f'{int(seconds) // 60:02}:{int(seconds) % 60:02}'
report = ['# 歌词核对（识别模型听到的内容对照歌词）', '']
for result in sorted(results, key=lambda r: -(len(r['unheard']) + len(r['extra']))):
    if not result['unheard'] and not result['extra']:
        continue
    report += [f"## {result['name']}", '']
    if result['unheard']:
        report += ['歌词里有、但没听到：'] + [f"- 第 {item['line']} 行{'（约 ' + clock(item['time']) + '）' if item['time'] is not None else ''}：{item['text']}" for item in result['unheard']] + ['']
    if result['extra']:
        report += ['听到了、但歌词里没有：'] + [f"- 约 {clock(item['around'])} 起的 20 秒内：{item['heard']}" for item in result['extra']] + ['']
(OUT / '歌词核对.md').write_text('\n'.join(report), encoding='utf-8')
for result in results:
    print(f"{len(result['unheard']):>3} unheard {len(result['extra']):>3} extra  {result['name']}")
