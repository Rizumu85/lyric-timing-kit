"""Turn readings read off karaoke videos (backups/video/<key>/readings.txt, kana or romaji,
one sung line per text line) into per-kanji reading differences, in the same shape
furigana_audit.mjs writes, so apply_furigana.mjs can apply them:

    python video_audit.py            -> <data>/video-audit.json
    node apply_furigana.mjs --write video-audit.json

A lyric row is matched against one video line or up to three consecutive ones (videos often
show half lines). Each kanji run first tries our own reading, so only runs the video really
reads differently are reported.
"""
import json
import re
from difflib import SequenceMatcher

import romkan

from batch_inventory import OUT
from kit_config import VIDEO

hira = lambda text: ''.join(chr(ord(c) - 96) if 'ァ' <= c <= 'ヶ' else c for c in text)
squash = lambda text: re.sub(r'[^ぁ-ゖーa-z0-9]', '', hira(text.lower()))
# Romaji writes particles by sound and long vowels in several ways; accept either in fixed text.
LOOSE = {'は': '[はわ]', 'へ': '[へえ]', 'を': '[をお]', 'ー': '[ーあいうえお]?', 'づ': '[づず]', 'ぢ': '[ぢじ]', 'お': '[おを]', 'わ': '[わは]', 'え': '[えへ]'}


def sung(line):
    """A video line as kana: romaji is converted, kana is kept."""
    if re.search('[ぁ-ヿ]', line):
        return squash(line)
    return squash(romkan.to_hiragana(re.sub(r'[^a-z\'-]', '', line.lower())))


def line_diffs(annotated, kana):
    runs, pattern = [], ''
    for part in re.split(r'(\{[^|{}]+\|[^{}]+\})', annotated):
        match = re.fullmatch(r'\{([^|]+)\|([^}]+)\}', part)
        if match and runs and pattern.endswith('\x00'):
            runs[-1]['base'] += match[1]; runs[-1]['ours'] += hira(match[2]); runs[-1]['parts'].append([match[1], hira(match[2])])
        elif match:
            runs.append({'base': match[1], 'ours': hira(match[2]), 'parts': [[match[1], hira(match[2])]]}); pattern += '\x00'
        else:
            pattern += ''.join(LOOSE.get(char, re.escape(char)) for char in squash(part))
    if not runs:
        return []
    groups = iter(runs)
    pattern = re.sub('\x00', lambda _: '(' + re.escape(next(groups)['ours']) + '|.+?)', pattern)
    found = re.fullmatch(pattern, kana)
    if not found:
        return None
    return [{**run, 'theirs': found[index + 1]} for index, run in enumerate(runs)
            if found[index + 1] != run['ours'] and re.fullmatch('[ぁ-ゖー]+', found[index + 1])]


report = []
for file in sorted((VIDEO).glob('*/readings.txt')):
    key = file.parent.name
    text = file.read_text(encoding='utf-8')
    if text.startswith('UNUSABLE') or not (OUT / key / 'prepared.json').exists():
        continue
    video = [sung(line) for line in text.split('\n') if sung(line)]
    rows = json.loads((OUT / key / 'prepared.json').read_text(encoding='utf-8'))['rows']
    source = json.loads((OUT / key / 'source.json').read_text(encoding='utf-8'))
    diffs, compared, lines = [], 0, []
    for index, row in enumerate(rows):
        annotated = row.get('annotated', '')
        if '{' not in annotated:
            continue
        ours = squash(re.sub(r'\{[^|]+\|([^}]+)\}', r'\1', annotated))
        best, best_ratio = None, 0
        for start in range(len(video)):
            for width in (1, 2, 3):
                candidate = ''.join(video[start:start + width])
                ratio = SequenceMatcher(None, ours, candidate, autojunk=False).ratio()
                if ratio < .75:
                    continue
                found = line_diffs(annotated, candidate)
                # the closest-sounding stretch of the video is the same line; anything else is another line
                if found is not None and (best is None or ratio > best_ratio):
                    best, best_ratio = found, ratio
        if best is None:
            continue
        compared += 1
        lines.append(index + 1)
        diffs += [{'line': index + 1, 'text': row['original'], **diff} for diff in best
                  if re.fullmatch(r'[㐀-鿿々]+', diff['base']) and not diff['theirs'].startswith('ー') and len(diff['theirs']) <= len(diff['base']) * 3 + 1]
    report.append({'key': key, 'name': source['name'], 'status': 'checked', 'url': 'video', 'share': round(compared / max(1, sum('{' in r.get('annotated', '') for r in rows)), 2), 'compared': compared, 'rows': lines, 'diffs': diffs})
    if report[-1]['share'] < 0.5: report[-1]['status'] = 'partial'  # too few of the song's lines were matched to call its readings checked
    print(f"{source['name']}: compared {compared} rows, {len(diffs)} differences")
    for diff in diffs:
        print(f"    第 {diff['line']} 行 {diff['base']}: {diff['ours']} -> {diff['theirs']}")
(OUT / 'video-audit.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
