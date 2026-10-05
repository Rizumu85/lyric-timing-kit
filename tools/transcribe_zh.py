"""Transcribe sung Chinese (and Chinese-English) songs with FireRedASR2-AED.

Same output as transcribe_song.py (<data>/<key>/heard.json), in overlapping
20 s windows. A transcript from the other model is kept beside it as heard.qwen.json.
Usage: python (ASR environment) transcribe_zh.py <key> [<key> ...] | --keys-file <file>
"""
import json
import sys
import tempfile
from pathlib import Path

import librosa
import soundfile

ROOT = Path(__file__).resolve().parent
from kit_config import OUT, MODELS, FIRERED
sys.path.insert(0, str(FIRERED))
from fireredasr2s.fireredasr2 import FireRedAsr2, FireRedAsr2Config  # noqa: E402
from audio_io import load_audio

MODEL = 'FireRedASR2-AED'
WINDOW, HOP, RATE = 20, 10, 16000

config = FireRedAsr2Config(use_gpu=True, use_half=False, beam_size=3, nbest=1, decode_max_len=0, softmax_smoothing=1.25, aed_length_penalty=0.6, eos_penalty=1.0, return_timestamp=False)
model = FireRedAsr2.from_pretrained('aed', str(MODELS / 'asr-models' / MODEL), config)

args = sys.argv[1:]
keys = open(args[1], encoding='utf-8').read().split() if args[:1] == ['--keys-file'] else args
work = Path(tempfile.mkdtemp(prefix='firered-'))
for key in keys:
    target = OUT / key / 'heard.json'
    if target.exists():
        old = json.loads(target.read_text(encoding='utf-8'))
        if old.get('model') == MODEL:
            continue
        (OUT / key / 'heard.qwen.json').write_text(json.dumps(old, ensure_ascii=False, indent=2), encoding='utf-8')
    source = json.loads((OUT / key / 'source.json').read_text(encoding='utf-8'))
    wave = load_audio(source['audio'], RATE)
    chunks = []
    for start in range(0, max(1, int(len(wave) / RATE) - HOP // 2), HOP):
        piece = work / 'piece.wav'
        soundfile.write(piece, wave[start * RATE:(start + WINDOW) * RATE], RATE, subtype='PCM_16')
        text = model.transcribe(['piece'], [str(piece)])[0]['text']
        chunks.append({'start': start, 'end': min(start + WINDOW, round(len(wave) / RATE, 1)), 'text': text})
        print(f"{source['name']} {start:>3}s {text}", flush=True)
    target.write_text(json.dumps({'model': MODEL, 'chunks': chunks}, ensure_ascii=False, indent=2), encoding='utf-8')
