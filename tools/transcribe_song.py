"""Transcribe what is actually sung in a recording, in overlapping windows.

Used for covers and short versions, to find which lines of the full lyrics the singer sang.
Writes <data>/<key>/heard.json.
Usage: python (ASR environment) transcribe_song.py <key> [<key> ...]
"""
import json
import sys

import librosa
import torch
from qwen_asr import Qwen3ASRModel
from audio_io import load_audio
from kit_config import OUT

LANGUAGES = {'ja': 'Japanese', 'en': 'English', 'zh': 'Chinese', 'ko': 'Korean'}
WINDOW, HOP, RATE = 20, 10, 16000

model = Qwen3ASRModel.from_pretrained('Qwen/Qwen3-ASR-1.7B', dtype=torch.bfloat16, device_map='cuda:0', max_new_tokens=256)
args = sys.argv[1:]
keys = open(args[1], encoding='utf-8').read().split() if args[:1] == ['--keys-file'] else args
for key in keys:
    if (OUT / key / 'heard.json').exists():
        continue  # already transcribed
    source = json.loads((OUT / key / 'source.json').read_text(encoding='utf-8'))
    wave = load_audio(source['audio'], RATE)
    chunks = []
    for start in range(0, max(1, int(len(wave) / RATE) - HOP // 2), HOP):
        piece = wave[start * RATE:(start + WINDOW) * RATE]
        text = model.transcribe(audio=(piece, RATE), language=LANGUAGES.get(source['language']))[0].text
        chunks.append({'start': start, 'end': min(start + WINDOW, round(len(wave) / RATE, 1)), 'text': text})
        print(f"{source['name']} {start:>3}s {text}", flush=True)
    (OUT / key / 'heard.json').write_text(json.dumps({'model': 'Qwen/Qwen3-ASR-1.7B', 'chunks': chunks}, ensure_ascii=False, indent=2), encoding='utf-8')
