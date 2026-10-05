"""Decode any audio file to mono samples through ffmpeg, so that every format Folia plays can be
read the same way (the audio libraries used elsewhere only open some of them)."""
import subprocess

import numpy as np


def load_audio(path, rate):
    """The first audio stream of `path` as float32 mono at `rate` Hz."""
    run = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:a:0', '-ac', '1', '-ar', str(rate), '-f', 'f32le', '-'], capture_output=True)
    if run.returncode or not run.stdout:
        raise RuntimeError('无法解码音频：' + run.stderr.decode('utf-8', 'replace').strip()[-300:])
    return np.frombuffer(run.stdout, dtype=np.float32).copy()
