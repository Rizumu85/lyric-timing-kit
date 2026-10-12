"""Find kanji words whose marked reading does not sound like what is sung, without knowing what
the right reading would be: decode the vocal track freely over the word's own time span (the
letters the acoustic model hears there) and measure how far that is from the marked reading.

    python reading_outliers.py --calibrate          measure on verified lines how well this separates right from wrong readings
    python reading_outliers.py <key> [...] | --keys-file <file>
                                                                      write <key>/reading-outliers.json

A word is reported when its distance is above THRESHOLD (see the calibration numbers printed by
--calibrate; long held notes, harmonies and fast passages make right readings look wrong too).

A second listener trained on other recordings (RomajiASR, see onepass.py) hears the same span.
When it is installed a word is reported only if BOTH find the reading far from what they hear,
at the lower threshold BOTH: as many right readings flagged as before, more wrong ones caught.
"""
import json
import random
import re
import sys

import romkan
import torch

from batch_align import OUT

BLANK, FRAME, THRESHOLD, BOTH = 29, 0.02, 0.4, 0.3
LETTERS = {index: chr(96 + index) for index in range(1, 27)}


def letters(kana):
    text = romkan.to_hepburn(kana.replace('ー', '')).lower()
    return re.sub('[^a-z]', '', text)


def heard(emission, start, end):
    left, right = max(0, int(start / FRAME) - 3), min(len(emission), int(end / FRAME) + 4)
    ids = emission[left:right].argmax(dim=1).tolist()
    out, last = [], None
    for token in ids:
        if token != last and token != BLANK and token in LETTERS:
            out.append(LETTERS[token])
        last = token
    return ''.join(out)


def distance(a, b):
    """Edit distance over the longer length; vowel length (aa/a, ou/o) is not counted."""
    a, b = re.sub(r'(.)\1+', r'\1', a.replace('ou', 'o')), re.sub(r'(.)\1+', r'\1', b.replace('ou', 'o'))
    if not a or not b:
        return 1.0
    # best match of the reading b anywhere inside the heard text a (the word's own time span is
    # not trusted: a slightly shifted span would make a right reading look wrong)
    row = [0] * (len(a) + 1)
    for j, y in enumerate(b, 1):
        new = [j]
        for i, x in enumerate(a, 1):
            new.append(min(row[i] + 1, new[i - 1] + 1, row[i - 1] + (x != y)))
        row = new
    return min(row) / len(b)


def words(key):
    """(line number, kanji, reading, start, end) for every kanji word with a reading in the draft."""
    lines = json.loads((OUT / key / 'aligned.fia').read_text(encoding='utf-8'))['lyrics']['lines']
    for number, line in enumerate(lines, 1):
        for word in line.get('words', []):
            parts = [part for part in word.get('syllables', []) if part.get('ruby')] or ([word] if word.get('ruby') else [])
            for part in parts:
                reading = ''.join(item['text'] for item in part['ruby'])
                if re.search('[㐀-鿿々]', part['text']) and reading and part['endTime'] > part['startTime']:
                    yield number, part['text'].strip(), reading, part['startTime'], part['endTime']


def measure(key):
    import onepass
    emission = torch.load(OUT / key / 'ja-emission.pt', weights_only=True, map_location='cpu')
    lines = json.loads((OUT / key / 'aligned.fia').read_text(encoding='utf-8'))['lyrics']['lines']
    # the second listener's sounds: [[sound, frame], ...] (None when it is not installed)
    sounds = None
    if onepass.second_reading(OUT / key, json.loads((OUT / key / 'source.json').read_text(encoding='utf-8')), onepass.tokenizer()) is not None:
        sounds = json.loads((OUT / key / 'second-reading.json').read_text(encoding='utf-8'))
    whole = {}
    for number, kanji, reading, start, end in words(key):
        if number not in whole:  # everything heard over the line, with a little room on both sides
            a, b = lines[number - 1]['startTime'] - 0.4, lines[number - 1]['endTime'] + 0.4
            other = None if sounds is None else re.sub('[^a-z]', '', ''.join(text for text, frame in sounds if a / FRAME - 3 <= frame < b / FRAME + 4))
            whole[number] = (heard(emission, a, b), other)
        sound, other = whole[number]
        item = {'line': number, 'base': kanji, 'reading': reading, 'heard': sound, 'distance': round(distance(sound, letters(reading)), 2), 'letters': len(letters(reading))}
        if other is not None:
            item['heard2'], item['distance2'] = other, round(distance(other, letters(reading)), 2)
        yield item


def doubtful(item):
    """The reading does not sound like what is sung: to both listeners, or to the only one."""
    if 'distance2' in item:
        return item['distance'] > BOTH and item['distance2'] > BOTH
    return item['distance'] > THRESHOLD


args = sys.argv[1:]
if args[:1] == ['--calibrate']:
    # lines whose readings a source vouches for
    verified = {}
    for name in ('furigana-audit.json', 'moegirl-audit.json', 'video-audit.json'):
        for entry in json.loads((OUT / name).read_text(encoding='utf-8')):
            if entry.get('rows'):
                verified.setdefault(entry['key'], set()).update(entry['rows'])
    random.seed(1)
    right, wrong, pool, both_wrong = [], [], [], []
    for key in random.sample(sorted(verified), min(80, len(verified))):
        if not (OUT / key / 'ja-emission.pt').exists() or not (OUT / key / 'aligned.fia').exists():
            continue
        found = [item for item in measure(key) if item['line'] in verified[key]]
        right += found
        pool += [item['reading'] for item in found]
    for item in right:  # the same sound against another word's reading of similar length = a wrong reading
        others = [reading for reading in pool if reading != item['reading'] and abs(len(reading) - len(item['reading'])) <= 1]
        if others:
            other = letters(random.choice(others))
            wrong.append(distance(item['heard'], other))
            if 'heard2' in item:
                both_wrong.append((wrong[-1], distance(item['heard2'], other)))
    print(f'{len(right)} verified words, {len(wrong)} simulated wrong readings')
    for threshold in (0.3, 0.4, 0.5, 0.6, 0.7, 0.8):
        false_alarm = sum(item['distance'] > threshold for item in right) / len(right)
        caught = sum(value > threshold for value in wrong) / len(wrong)
        print(f'threshold {threshold}: right readings flagged {false_alarm:.1%}, wrong readings caught {caught:.1%}')
    two = [item for item in right if 'distance2' in item]
    if two and both_wrong:
        print(f'with the second listener ({len(two)} words): reported only when both find the reading wrong')
        for threshold in (0.25, 0.3, 0.35, 0.4):
            false_alarm = sum(item['distance'] > threshold and item['distance2'] > threshold for item in two) / len(two)
            caught = sum(a > threshold and b > threshold for a, b in both_wrong) / len(both_wrong)
            print(f'  threshold {threshold}: right readings flagged {false_alarm:.1%}, wrong readings caught {caught:.1%}')
    long_words = [item for item in right if len(letters(item['reading'])) >= 4]
    print('words of 4+ letters only:')
    for threshold in (0.5, 0.6, 0.7):
        print(f'  threshold {threshold}: right readings flagged {sum(i["distance"] > threshold for i in long_words) / len(long_words):.1%}')
else:
    keys = open(args[1], encoding='utf-8').read().split() if args[:1] == ['--keys-file'] else args
    for key in keys:
        try:
            found = [item for item in measure(key) if doubtful(item) and item['letters'] >= 4]
        except FileNotFoundError:
            continue
        (OUT / key / 'reading-outliers.json').write_text(json.dumps(found, ensure_ascii=False, indent=2), encoding='utf-8')
        print(key, len(found), flush=True)
