"""Separate the vocals of songs with BS-RoFormer and cache the aligner's emissions, so the
alignment of new songs starts from the better separation model.
Songs that already have an emission cache are skipped.
Usage: python prepare_emission.py <key> [<key> ...] | --keys-file <file>
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

import torch
import torchaudio

ROOT = Path(__file__).resolve().parent
from batch_align import OUT  # noqa: E402
from audio_separator.separator import Separator  # noqa: E402
from kit_config import MODELS

args = sys.argv[1:]
keys = open(args[1], encoding='utf-8').read().split() if args[:1] == ['--keys-file'] else args
cache = lambda key, language: OUT / key / ('ja-emission.pt' if language == 'ja' else 'mms-emission.pt')
todo = []
for key in keys:
    source = json.loads((OUT / key / 'source.json').read_text(encoding='utf-8'))
    if source.get('rows') and not cache(key, source['language']).exists():
        todo.append((key, source))

work = Path(tempfile.mkdtemp(prefix='sep-'))
separator = Separator(output_dir=str(work), model_file_dir=str(MODELS / 'sep-models'), output_single_stem='Vocals')
separator.load_model(model_filename='model_bs_roformer_ep_317_sdr_12.9755.ckpt')
vocals = {}
for key, source in todo:
    neutral = work / f'{key}.mp3'  # the separator names its output after the input file
    shutil.copyfile(source['audio'], neutral)
    produced = separator.separate(str(neutral))
    wave, rate = torchaudio.load(str(work / Path(produced[0]).name))
    vocals[key] = torchaudio.functional.resample(wave.mean(0), rate, 16000)
    print('separated', source['name'], flush=True)
del separator
torch.cuda.empty_cache()

from batch_align import Pipeline  # noqa: E402
pipeline = Pipeline()
for key, source in todo:
    torch.save(pipeline.emissions(vocals[key], source['language']), cache(key, source['language']))
    print('emissions', source['name'], flush=True)
shutil.rmtree(work, ignore_errors=True)
