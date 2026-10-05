"""Take line start times from a karaoke video of the same recording.

1. Check the video really is the same recording as the MP3 and find the time offset between
   them (cross-correlation of the two audio tracks' onset strength).
2. Watch the two subtitle slots of the video. A lyric line appears as white text with a dark
   outline and changes colour from left to right as it is sung. Once a line has appeared, the
   pixels of its text are remembered; the line starts at the moment those pixels begin to lose
   their white. Looking only at the remembered text pixels keeps a busy background out of it.
3. Compare those moments (in MP3 time) with the current line starts.

Usage: python (ASR environment) video_timing.py <key>
Needs backups/video/<key>/video.mp4 and an audio file in backups/video/<key>/audio/.
"""
import json
import subprocess
import sys
from pathlib import Path

import librosa
import numpy as np
from kit_config import OUT, VIDEO
from audio_io import load_audio

ROOT = Path(__file__).resolve().parent
key = sys.argv[1]
folder = VIDEO / key
source = json.loads((OUT / key / 'source.json').read_text(encoding='utf-8'))
lines = json.loads((OUT / key / 'aligned.fia').read_text(encoding='utf-8'))['lyrics']['lines']
edits_file = OUT / key / 'timing-edits.applied.json'
edits = json.loads(edits_file.read_text(encoding='utf-8')) if edits_file.exists() else {'l': {}}

# 1. same recording? offset?
RATE, HOP = 22050, 512
song = load_audio(source['audio'], RATE)
wave_file = folder / 'audio.wav'  # the downloaded track is m4a, which the audio library cannot open
if not wave_file.exists():
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(next((folder / 'audio').iterdir())), '-ac', '1', '-ar', str(RATE), str(wave_file)], check=True)
video_audio, _ = librosa.load(str(wave_file), sr=RATE, mono=True)
a = librosa.onset.onset_strength(y=song, sr=RATE, hop_length=HOP)
b = librosa.onset.onset_strength(y=video_audio, sr=RATE, hop_length=HOP)
a, b = (a - a.mean()) / a.std(), (b - b.mean()) / b.std()
correlation = np.correlate(a, b, mode='full') / min(len(a), len(b))
lag = int(correlation.argmax()) - (len(b) - 1)
offset = lag * HOP / RATE  # song time = video time + offset
print(f'audio match: peak correlation {correlation.max():.2f}; song time = video time {offset:+.2f} s')

# 2. per slot: remember a line's text pixels, then watch them lose their white
FPS, W, H = 15, 640, 360
SLOTS = {'top': (228, 296, 20, 440), 'bottom': (296, 356, 200, 630)}  # y0, y1, x0, x1
process = subprocess.Popen(['ffmpeg', '-v', 'error', '-i', str(folder / 'video.mp4'), '-vf', f'fps={FPS},scale={W}:{H}', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], stdout=subprocess.PIPE)


def text_mask(box):
    white = box.min(axis=2) > 215
    dark = box.max(axis=2) < 70
    near = np.zeros_like(dark)
    for dy in range(-3, 4):
        for dx in range(-3, 4):
            near |= np.roll(np.roll(dark, dy, axis=0), dx, axis=1)
    return white & near  # white with a dark outline within 3 px: lyric text, not white background


state = {name: {'recent': [], 'mask': None, 'size': 0, 'fired': False} for name in SLOTS}
events, index = [], 0
while True:
    raw = process.stdout.read(W * H * 3)
    if len(raw) < W * H * 3:
        break
    frame = np.frombuffer(raw, np.uint8).reshape(H, W, 3)
    for name, (y0, y1, x0, x1) in SLOTS.items():
        slot, box = state[name], frame[y0:y1, x0:x1]
        current = text_mask(box)
        slot['recent'] = (slot['recent'] + [current])[-3:]
        stable = np.logical_and.reduce(slot['recent']) if len(slot['recent']) == 3 else None
        if slot['mask'] is not None:
            still = (box.min(axis=2)[slot['mask']] > 200).mean()
            if not slot['fired'] and still < .95:
                # the wipe must start at the left end of the line, or it is not a wipe
                columns = np.flatnonzero(slot['mask'].any(axis=0))
                left = slot['mask'].copy()
                left[:, columns[0] + max(8, (columns[-1] - columns[0]) // 5):] = False
                if (box.min(axis=2)[left] > 200).mean() < .8:
                    events.append((index / FPS + offset, name, slot['size']))
                    slot['fired'] = True
            if still < .15:
                slot['mask'] = None  # this line is finished
        if stable is not None and stable.sum() > 350:
            # a (new) line is standing still in the slot: remember its text pixels
            if slot['mask'] is None or (stable & ~slot['mask']).sum() > max(200, slot['size'] * .4):
                slot.update(mask=stable, size=int(stable.sum()), fired=False)
    index += 1
process.wait()
events.sort()
print(f'{len(events)} line starts seen in the video; {len(lines)} lyric lines')

# 3. compare with the line starts as they are now (including the user's adjustments)
truth = [line['startTime'] for line in lines]
used, report = set(), []
for number, start in enumerate(truth):
    near = [(abs(time - start), n) for n, (time, _, _) in enumerate(events) if n not in used and abs(time - start) <= 2.5]
    video = None
    if near:
        _, n = min(near)
        used.add(n)
        video = round(events[n][0], 2)
    report.append((number + 1, start, video, source['rows'][number].get('sourceStart'), lines[number]['fullText']))
diffs = np.array([video - ours for _, ours, video, _, _ in report if video is not None])
print(f'matched {len(diffs)} of {len(lines)} lines; within 0.3 s of the current start: {(np.abs(diffs) <= .3).sum()}; within 0.5 s: {(np.abs(diffs) <= .5).sum()}')
print(f'video minus current start: median {np.median(diffs):+.2f} s, spread (IQR) {np.subtract(*np.percentile(diffs, [75, 25])):.2f} s')
for number, ours, video, sheet_time, text in report:
    print(f"{number:>3}  now {ours:7.2f}  video {'' if video is None else format(video, '7.2f'):>7}  platform {'' if sheet_time is None else format(sheet_time, '7.2f'):>7}  {text[:26]}")
(folder / 'video-timing.json').write_text(json.dumps({'offset': offset, 'events': events, 'report': report}, ensure_ascii=False, indent=2), encoding='utf-8')
