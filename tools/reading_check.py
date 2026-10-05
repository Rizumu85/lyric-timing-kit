"""Acoustic cross-check of kanji readings.

For every kanji word whose dictionary offers more than one reading, align the line once per
reading against the song's vocal emissions and see which reading the audio supports. Needs no
lyric site, so it also covers songs no furigana source was found for.
Writes <data>/<key>/reading-check.json and prints one line per finding.
Usage: python reading_check.py <key> [<key> ...] | --keys-file <file>
"""
import json
import re
import sys

import numpy as np
import torch

from batch_align import OUT, Pipeline, hira, hn, align_yohane

KANJI = r'㐀-鿿々'
WORD = re.compile(rf'^([{KANJI}]+)([ぁ-ゖ]*)$')
# Sung readings the dictionary does not list.
EXTRA = {'私': ['あたし', 'わたし'], '今日': ['きょう'], '明日': ['あした', 'あす'], '昨日': ['きのう'], '一人': ['ひとり'], '二人': ['ふたり'], '何処': ['どこ'], '貴方': ['あなた'], '此処': ['ここ'], '運命': ['うんめい', 'さだめ'], '瞬間': ['しゅんかん', 'とき'], '未来': ['みらい', 'あす'], '宇宙': ['うちゅう', 'そら'], '地球': ['ちきゅう', 'ほし'], '理由': ['りゆう', 'わけ'], '永遠': ['えいえん', 'とわ'], '想い': ['おもい'], '時間': ['じかん', 'とき'], '女': ['おんな', 'ひと'], '男': ['おとこ', 'ひと'], '他人': ['たにん', 'ひと'], '本気': ['ほんき', 'まじ'], '真実': ['しんじつ', 'ほんとう'], '生命': ['せいめい', 'いのち'], '故郷': ['こきょう', 'ふるさと']}

args = sys.argv[1:]
keys = open(args[1], encoding='utf-8').read().split() if args[:1] == ['--keys-file'] else args
pipeline = Pipeline()
dictionary = pipeline.tokenizer.sys_dic


def readings(surface):
    found = []
    for entry in dictionary.lookup(surface.encode('utf8'), pipeline.tokenizer.matcher) if hasattr(pipeline.tokenizer, 'matcher') else dictionary.lookup(surface.encode('utf8')):
        if entry[1] == surface:
            extra = dictionary.lookup_extra(entry[0])
            if '固有名詞' in extra[0]:
                continue  # readings of personal and place names (未来 = みき) are not lyric readings
            reading = hira(extra[4])
            if reading and reading != '*' and reading not in found:
                found.append(reading)
    return found


def score(text, emission, left, right):
    units = hn.process_haruhi_line(pipeline.annotate(text), 'jaen')
    prons = [re.sub("[^a-z']", '', unit.get('pron', '').lower()) for unit in units]
    prons = [p for p in prons if p]
    spans = align_yohane._align_token_spans(emission[left:right], pipeline.japanese.tokenize(prons), blank=pipeline.japanese.blank)
    return float(np.mean([s.score for group in spans for s in group]))


for key in keys:
    folder = OUT / key
    try:
        source = json.loads((folder / 'source.json').read_text(encoding='utf-8'))
        if source['language'] != 'ja':
            continue
        rows = json.loads((folder / 'prepared.json').read_text(encoding='utf-8'))['rows']
        lines = json.loads((folder / 'aligned.fia').read_text(encoding='utf-8'))['lyrics']['lines']
        emission = torch.load(folder / 'ja-emission.pt', weights_only=True, map_location='cpu')
    except FileNotFoundError:
        continue
    findings = []
    for number, (row, line) in enumerate(zip(rows, lines), 1):
        text = row['original']
        if row.get('nonVocal') or re.search(r'[{}\[\]()（）]', text):
            continue
        left, right = max(0, int((line['startTime'] - .3) / .02)), min(len(emission), int((line['endTime'] + .5) / .02))
        position, base_score = 0, None
        for token in pipeline.tokenizer.tokenize(text):
            surface, start = token.surface, position
            position += len(surface)
            word = WORD.match(surface)
            if not word or text[start:start + len(surface)] != surface:
                continue
            run, tail = word.groups()
            before, after = text[start - 1:start], text[start + len(run):start + len(run) + 1]
            if re.match(rf'[{KANJI}]', before or ' ') or re.match(rf'[{KANJI}]', after or ' '):
                continue  # part of a longer written kanji run; a reading cannot be attached to it alone
            default = hira(token.reading)
            options = [r for r in readings(surface) + [e + tail for e in EXTRA.get(run, [])] if r != default and r.endswith(tail)]
            if not options or not default.endswith(tail):
                continue
            cut = lambda reading: reading[:len(reading) - len(tail)] if tail else reading
            try:
                if base_score is None:
                    base_score = score(text, emission, left, right)
                best, best_score = None, base_score
                for option in dict.fromkeys(options):
                    if not cut(option):
                        continue
                    value = score(text[:start] + run + '(' + cut(option) + ')' + text[start + len(run):], emission, left, right)
                    if value > best_score:
                        best, best_score = option, value
            except Exception:
                continue
            if best is None:
                continue
            current = re.search(r'\{' + re.escape(run) + r'\|([^}]+)\}', row.get('annotated', ''))
            findings.append({'line': number, 'text': text, 'base': run, 'default': cut(default), 'heard': cut(best), 'gain': round(best_score - base_score, 3), 'lineScore': round(base_score, 3), 'current': current[1] if current else None})
    (folder / 'reading-check.json').write_text(json.dumps(findings, ensure_ascii=False, indent=2), encoding='utf-8')
    for finding in findings:
        print(json.dumps({'song': source['name'], **finding}, ensure_ascii=False), flush=True)
