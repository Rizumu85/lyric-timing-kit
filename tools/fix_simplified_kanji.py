"""Japanese lyrics copied from Chinese sites often have simplified Chinese characters in place
of the Japanese kanji (违う for 違う, 语りかける for 語りかける). The dictionary cannot read
those, so the readings come out as nonsense. Convert them to the Japanese forms.

Only characters in lines that contain kana are touched, and only when the simplified form is
not itself a character used in Japanese. Prints every change; pass --write to save and list
the changed keys in readings-changed.txt.
Usage: python fix_simplified_kanji.py [--write]
"""
import json
import re
import sys

from opencc import OpenCC

from batch_inventory import OUT

to_traditional, to_japanese = OpenCC('s2t'), OpenCC('t2jp')
write = '--write' in sys.argv


def is_japanese(char):
    """A character Japanese text can contain: it survives a Shift-JIS round trip."""
    try:
        char.encode('shift_jis')
        return True
    except UnicodeEncodeError:
        return False


# Simplified forms that happen to exist in the Japanese character set but are not used in Japanese text.
ALSO_SIMPLIFIED = {'梦': '夢', '弃': '棄', '妆': '粧'}


def convert(text):
    out = []
    for char in text:
        if char in ALSO_SIMPLIFIED:
            out.append(ALSO_SIMPLIFIED[char])
        elif '㐀' <= char <= '鿿' and not is_japanese(char):
            new = to_japanese.convert(to_traditional.convert(char))
            out.append(new if len(new) == 1 and is_japanese(new) else char)
        else:
            out.append(char)
    return ''.join(out)


changed, total = [], 0
for folder in sorted(OUT.iterdir()):
    file = folder / 'source.json'
    if not file.exists() or folder.name.endswith('-rf'):
        continue
    source = json.loads(file.read_text(encoding='utf-8'))
    if source.get('language') != 'ja' or not isinstance(source.get('rows'), list):
        continue
    hits = []
    for row in source['rows']:
        if not re.search('[ぁ-ヿ]', row['original']):
            continue  # a line without kana may simply be Chinese
        new = convert(row['original'])
        if new != row['original']:
            hits.append((row['original'], new))
            row['original'] = new
    if hits:
        total += len(hits)
        print(f"{source['name']}: {len(hits)} lines, e.g. {hits[0][0]} -> {hits[0][1]}")
        if write:
            file.write_text(json.dumps(source, ensure_ascii=False, indent=2), encoding='utf-8')
            changed.append(folder.name)
if write:
    (OUT / 'readings-changed.txt').write_text('\n'.join(changed) + '\n', encoding='utf-8')
print(f'{total} lines in {len(changed) if write else "?"} songs')
