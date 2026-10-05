"""Where everything lives on this machine. Read from lyric-kit.config.json in the repository
root (or the file named by LYRIC_KIT_CONFIG); see lyric-kit.config.example.json.
"""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent          # tools/
REPO = ROOT.parent
_file = Path(os.environ.get('LYRIC_KIT_CONFIG') or REPO / 'lyric-kit.config.json')
if not _file.exists():
    raise SystemExit(f'No configuration: copy lyric-kit.config.example.json to {_file} and set musicDir')
_config = json.loads(_file.read_text(encoding='utf-8'))
if not _config.get('musicDir'):
    raise SystemExit(f'{_file}: musicDir is required')


def _at(value, fallback=None):
    return (_file.parent / (value or fallback)).resolve()


MUSIC = _at(_config['musicDir'])
_DATA = _at(_config.get('dataDir'), REPO / 'data')
OUT = _at(_config.get('songsDir'), _DATA / 'songs')            # one folder per song
BACKUPS = _at(_config.get('backupDir'), _DATA / 'backups')     # untouched copies of every MP3 before it is written
VIDEO = _at(_config.get('videoDir'), _DATA / 'video')          # karaoke video frames and readings read off them
CACHE = _at(_config.get('cacheDir'), _DATA / 'cache')
MODELS = _at(_config.get('modelDir'), _DATA / 'models')
FA_KARA = _at(_config.get('faKaraDir'), REPO / 'vendor' / 'FA-Kara')
FIRERED = _at(_config.get('fireRedDir'), REPO / 'vendor' / 'FireRedASR2S')
# abbreviations of singer names used as labels in duet lyric sheets, e.g. {"k": "kana"}
SINGER_ALIASES = {str(key).casefold(): str(value).casefold() for key, value in (_config.get('singerAliases') or {}).items()}
for _dir in (OUT, BACKUPS, VIDEO, CACHE):
    _dir.mkdir(parents=True, exist_ok=True)
