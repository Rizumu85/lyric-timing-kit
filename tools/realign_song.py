"""Re-align songs' drafts from their own source.json, without touching the MP3.

Use after correcting lyric text or readings in <data>/<key>/source.json.
An explicit reading is written after the kanji, e.g. 一二三(ひふみ) or 憨八嘎(hanbaga).
Each song keeps the alignment mode it was made with (whole-song, or windowed by the old
line times when its notes say so).
Usage: python realign_song.py <key> [<key> ...]   or   --keys-file <file>
"""
import json
import sys
import traceback

from batch_align import OUT, Pipeline

args = sys.argv[1:]
force_windowed = '--windowed' in args
# --anchor-embedded: the line starts already in the MP3 are known to be good (the listener's own
# adjustments agree with them to about 0.15 s), so search for each line right at its embedded start.
anchor_embedded = '--anchor-embedded' in args
args = [arg for arg in args if arg not in ('--windowed', '--anchor-embedded', '--force-confirmed')]
keys = open(args[1], encoding='utf-8').read().split() if args[:1] == ['--keys-file'] else args
pipeline = Pipeline()
for number, key in enumerate(keys, 1):
    source = json.loads((OUT / key / 'source.json').read_text(encoding='utf-8'))
    previous = OUT / key / 'result.json'
    old = json.loads(previous.read_text(encoding='utf-8')) if previous.exists() else {}
    # A song the user confirmed by listening is finished: never re-align it as part of a batch.
    if old.get('confirmed') and '--force-confirmed' not in sys.argv:
        print(json.dumps({'n': number, 'of': len(keys), 'key': key, 'status': 'skipped', 'error': 'confirmed by the user'}, ensure_ascii=False), flush=True)
        continue
    pipeline.window_margin, pipeline.window_tail = 0.6, None
    if anchor_embedded:
        import re
        lrc = OUT / key / 'aligned.bilingual.lrc'
        starts = sorted({int(m[1]) * 60 + int(m[2]) + int(m[3]) / 100 + 0.05 for m in re.finditer(r'^\[(\d+):(\d+)\.(\d+)\]', lrc.read_text(encoding='utf-8'), re.M)}) if lrc.exists() else []
        if len(starts) != len(source['rows']):
            print(json.dumps({'n': number, 'of': len(keys), 'key': key, 'status': 'skipped', 'error': 'embedded lyrics have a different number of lines'}, ensure_ascii=False), flush=True)
            continue
        for row, start in zip(source['rows'], starts):
            row['sourceStart'] = round(start, 2)
        pipeline.window_margin, pipeline.window_tail = 0.15, 0.4
    windowed = force_windowed or anchor_embedded or any('原句时间窗' in issue for issue in old.get('issues', []) + source.get('issues', []))
    # Manual adjustments say where a line must start. Remember those times so they survive
    # the new alignment, whose own starts may differ slightly from the old ones.
    edits_file, fia_file, pinned = OUT / key / 'timing-edits.json', OUT / key / 'aligned.fia', {}
    if edits_file.exists() and fia_file.exists():
        edits = json.loads(edits_file.read_text(encoding='utf-8'))
        lines = json.loads(fia_file.read_text(encoding='utf-8'))['lyrics']['lines']
        pinned = {int(i): (lines[int(i)]['startTime'] + edits.get('g', 0) + e['d'], e['t']) for i, e in edits.get('l', {}).items() if int(i) < len(lines)}
    try:
        result = pipeline.process(source, embed=False, constrained_lines=windowed)
        if pinned:
            lines = json.loads(fia_file.read_text(encoding='utf-8'))['lyrics']['lines']
            kept = {str(i): {'d': round(target - lines[i]['startTime'] - edits.get('g', 0), 2), 't': text} for i, (target, text) in pinned.items() if i < len(lines) and lines[i]['fullText'] == text}
            edits_file.write_text(json.dumps({'g': edits.get('g', 0), 'l': {i: e for i, e in kept.items() if e['d']}}, ensure_ascii=False, indent=2), encoding='utf-8')
        # Re-aligning is not a reason to lose what the earlier run recorded about the file.
        if old.get('status') in ('complete', 'held_for_review'): result['wasStatus'] = old['status']
        elif old.get('wasStatus'): result['wasStatus'] = old['wasStatus']
        if old.get('backup'): result['backup'] = old['backup']
        (OUT / key / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'n': number, 'of': len(keys), 'name': result['name'], 'status': result['status'], 'score': round(result['meanScore'], 3), 'issues': result['issues']}, ensure_ascii=False), flush=True)
    except Exception as error:
        print(json.dumps({'n': number, 'of': len(keys), 'key': key, 'status': 'error', 'error': str(error), 'trace': traceback.format_exc()[-400:]}, ensure_ascii=False), flush=True)
