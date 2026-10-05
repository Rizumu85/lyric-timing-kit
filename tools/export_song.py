import csv
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

# Convert FA-Kara's timed intermediate structure to Folia's lossless lyric document.
ROOT = Path(__file__).resolve().parent

def seconds(tag):
    m = re.fullmatch(r'\[(\d+):(\d+):(\d+)\]', tag)
    if not m:
        raise ValueError(f'Invalid FA-Kara timestamp: {tag}')
    return int(m[1])*60+int(m[2])+int(m[3])/100

def lrc_time(t):
    cs = round(t*100)
    return f'[{cs//6000:02}:{cs//100%60:02}.{cs%100:02}]'

def retime(value, advance_seconds):
    # Shift all nested clocks together; metadata durations and text stay unchanged.
    if isinstance(value, list):
        return [retime(item, advance_seconds) for item in value]
    if isinstance(value, dict):
        return {key: max(0, round(item-advance_seconds, 6))
                if key in ('startTime', 'endTime') and isinstance(item, (int, float))
                else retime(item, advance_seconds) for key, item in value.items()}
    return value

def build_line(items, row):
    words = []
    ruby_count = 0
    i = 0
    pending = ''
    while i < len(items):
        item = items[i]
        if item['type'] == 2:
            if not item['orig']:
                raise ValueError('Orphan ruby continuation')
            group = [item]
            while i+1 < len(items) and items[i+1]['type'] == 2 and not items[i+1]['orig']:
                i += 1
                group.append(items[i])
            start, end = seconds(group[0]['start']), seconds(group[-1]['end'])
            ruby = [{'text': g['ruby'], 'startTime': seconds(g['start']), 'endTime': seconds(g['end'])} for g in group]
            syllable = {'text': item['orig'], 'startTime': start, 'endTime': end, 'ruby': ruby}
            words.append({'text': pending+item['orig'], 'startTime': start, 'endTime': end, 'syllables': [syllable]})
            pending = ''
            ruby_count += 1
        elif item.get('pron'):
            start, end = seconds(item['start']), seconds(item['end'])
            words.append({'text': pending+item['orig'], 'startTime': start, 'endTime': end})
            pending = ''
        elif words:
            words[-1]['text'] += item['orig']
        else:
            pending += item['orig']
        i += 1
    if pending or not words:
        raise ValueError('Line without timed words')
    full_text = ''.join(w['text'] for w in words)
    expected = re.sub(r'\{([^|]+)\|[^}]+\}', r'\1', row['annotated'])
    if full_text != expected:
        raise ValueError(f'Text changed: {full_text!r} != {expected!r}')
    return {'words': words, 'fullText': full_text, 'startTime': words[0]['startTime'],
            'endTime': words[-1]['endTime'], 'translation': row['translation']}, ruby_count
