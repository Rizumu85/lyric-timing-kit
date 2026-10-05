"""Tags of every audio format other than MP3: read title, artist and lyrics; write the two lyric
versions; set the title. The format is recognised from the file's contents and handled by the
kind of tag it carries, so a new extension needs no code here:

  kind     formats                          plain lyrics          Ruby lyrics (TimeTag with @Ruby)
  vorbis   FLAC, Ogg Vorbis, Opus           LYRICS                RUBY_LYRICS
  mp4      M4A, ALAC                        ©lyr                  ----:com.apple.iTunes:RUBY_LYRICS
  id3      WAV, AIFF, TTA, AAC              USLT, no descriptor   USLT, descriptor TimeTag-Ruby
  ape      APE, WavPack                     Lyrics                RUBY_LYRICS
  asf      WMA                              WM/Lyrics             RUBY_LYRICS

MP3s are written by batch_embed.mjs, which also builds and round-trip checks the texts and calls
this file for everything else. CAF has no tag library and is refused.

Writing backs the original up first, builds the new file beside it, and swaps it in only after
the audio packets and every other tag are shown to be unchanged.
Usage: python embed_tags.py <audio> <plain.lrc> <ruby.lrc or -> <backup folder> [language]   (prints JSON)
"""
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

from mutagen import File
from mutagen.aac import AAC
from mutagen.aiff import AIFF
from mutagen.asf import ASF
from mutagen.flac import FLAC
from mutagen.id3 import ID3, ID3NoHeaderError, TIT2, USLT
from mutagen.monkeysaudio import MonkeysAudio
from mutagen.mp4 import MP4, AtomDataType, MP4FreeForm
from mutagen.musepack import Musepack
from mutagen.oggflac import OggFLAC
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis
from mutagen.trueaudio import TrueAudio
from mutagen.wave import WAVE
from mutagen.wavpack import WavPack

# what Folia accepts as local music
AUDIO_EXTENSIONS = ('.mp3', '.flac', '.m4a', '.wav', '.ogg', '.opus', '.aac', '.alac', '.ape', '.wv', '.tta', '.wma', '.aif', '.aiff', '.caf')
RUBY = 'RUBY_LYRICS'
RUBY_DESCRIPTOR = 'TimeTag-Ruby'
FREEFORM = '----:com.apple.iTunes:' + RUBY
KINDS = (
    ((FLAC, OggVorbis, OggOpus, OggFLAC), 'vorbis'),
    ((MP4,), 'mp4'),
    ((WAVE, AIFF, TrueAudio), 'id3'),
    ((AAC,), 'id3-file'),  # no tag support of its own: an ID3 tag is put in front of the stream
    ((MonkeysAudio, WavPack, Musepack), 'ape'),
    ((ASF,), 'asf'),
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Song:
    """An audio file opened for its tags. `opener` re-opens another copy of the same format."""

    def __init__(self, path, opener=None):
        self.path = Path(path)
        # a raw AAC stream with an ID3 tag in front is otherwise taken for an MP3
        opener = opener or (AAC if self.path.suffix.lower() == '.aac' else None)
        self.audio = opener(self.path) if opener else File(self.path)
        if self.audio is None:
            raise SystemExit(f'{self.path.name}: 不认识的音频格式，或这种格式没有可写的标签')
        self.opener = type(self.audio)
        self.kind = next((kind for classes, kind in KINDS if isinstance(self.audio, classes)), None)
        if self.kind is None:
            raise SystemExit(f'{self.path.name}: {type(self.audio).__name__} 不在这里处理')
        if self.kind == 'id3-file':
            try:
                self.tags = ID3(self.path)
            except ID3NoHeaderError:
                self.tags = ID3()
        else:
            if self.audio.tags is None:
                self.audio.add_tags()
            self.tags = self.audio.tags

    @property
    def id3(self):
        return self.kind in ('id3', 'id3-file')

    def text(self, *names):
        """First value among these tag names, as text."""
        for name in names:
            try:
                value = self.tags[name]
            except (KeyError, ValueError):
                continue
            value = value[0] if isinstance(value, list) and value else value
            if isinstance(value, bytes):
                value = value.decode('utf-8', 'replace')
            if value:
                return str(value)
        return ''

    def lyrics(self):
        """(plain, ruby) as they are in the file; None where there is none."""
        if self.id3:
            frames = self.tags.getall('USLT')
            ruby = next((frame.text for frame in frames if frame.desc == RUBY_DESCRIPTOR), None)
            plain = next((frame.text for frame in frames if not frame.desc), None)
            if plain is None:
                plain = next((frame.text for frame in frames if frame.desc != RUBY_DESCRIPTOR and '@Ruby' not in frame.text), None)
            return plain, ruby
        if self.kind == 'mp4':
            ruby = self.tags.get(FREEFORM)
            return self.text('©lyr') or None, bytes(ruby[0]).decode('utf-8') if ruby else None
        plain = {'vorbis': ('LYRICS', 'UNSYNCEDLYRICS'), 'ape': ('Lyrics',), 'asf': ('WM/Lyrics',)}[self.kind]
        return self.text(*plain) or None, self.text(RUBY) or None

    def fields(self):
        names = {'vorbis': ('title', 'artist'), 'mp4': ('©nam', '©ART'), 'ape': ('Title', 'Artist'), 'asf': ('Title', 'Author')}
        title, artist = (self.text('TIT2'), self.text('TPE1')) if self.id3 else (self.text(names[self.kind][0]), self.text(names[self.kind][1]))
        duration = getattr(self.audio.info, 'length', 0) or 0
        if not duration:  # some M4A files do not state it where the tag library looks
            probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(self.path)], capture_output=True, text=True)
            duration = float(probe.stdout.strip() or 0)
        return {'title': title, 'artist': artist, 'lyrics': self.lyrics()[0] or '', 'duration': duration}

    def set_lyrics(self, plain, ruby, language='und'):
        if self.id3:
            for frame in self.tags.getall('USLT'):
                if not frame.desc or frame.desc == RUBY_DESCRIPTOR:
                    self.tags.delall(frame.HashKey)
            self.tags.add(USLT(encoding=1, lang=language, desc='', text=plain))
            if ruby:
                self.tags.add(USLT(encoding=1, lang=language, desc=RUBY_DESCRIPTOR, text=ruby))
            return
        plain_name = {'vorbis': 'LYRICS', 'mp4': '©lyr', 'ape': 'Lyrics', 'asf': 'WM/Lyrics'}[self.kind]
        ruby_name = FREEFORM if self.kind == 'mp4' else RUBY
        self.tags[plain_name] = plain if self.kind == 'ape' else [plain]
        if ruby:
            self.tags[ruby_name] = [MP4FreeForm(ruby.encode('utf-8'), dataformat=AtomDataType.UTF8)] if self.kind == 'mp4' else ruby if self.kind == 'ape' else [ruby]
        elif ruby_name in self.tags:
            del self.tags[ruby_name]

    def set_title(self, title):
        if self.id3:
            self.tags.delall('TIT2')
            self.tags.add(TIT2(encoding=1, text=[title]))
        else:
            name = {'vorbis': 'title', 'mp4': '©nam', 'ape': 'Title', 'asf': 'Title'}[self.kind]
            self.tags[name] = title if self.kind == 'ape' else [title]

    def save(self):
        if self.id3:
            # a tag that was ID3v2.3 stays so, and a new one is written as v2.3, the commonest
            old = getattr(self.tags, 'version', (2, 3, 0))
            version = 4 if old[:2] == (2, 4) and getattr(self.tags, 'size', 0) else 3
            if version == 3:
                self.tags.update_to_v23()
            if self.kind == 'id3-file':
                self.tags.save(self.path, v2_version=version)
            else:
                self.audio.save(v2_version=version)
        else:
            self.audio.save()

    def others(self, skip_title=False):
        """Every tag other than the lyrics (and the title, when that is what is being changed)."""
        if self.id3:
            skip = {frame.HashKey for frame in self.tags.getall('USLT') if not frame.desc or frame.desc == RUBY_DESCRIPTOR}
            skip |= {'TIT2'} if skip_title else set()
            return sorted((key, sha(repr(frame).encode())) for key, frame in self.tags.items() if key not in skip)
        mine = {'LYRICS', RUBY, '©LYR', FREEFORM.upper(), 'WM/LYRICS'} | ({'TITLE', '©NAM'} if skip_title else set())
        items = self.tags.as_dict().items() if self.kind == 'vorbis' else self.tags.items()
        rest = sorted((str(key).upper(), sha(repr(value).encode())) for key, value in items if str(key).upper() not in mine)
        pictures = [sha(picture.data) for picture in getattr(self.audio, 'pictures', [])]
        return rest + pictures


def audio_hash(path):
    """Hash of the encoded audio packets, whatever the container and its tags."""
    run = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:a:0', '-c', 'copy', '-f', 'hash', '-hash', 'sha256', '-'], capture_output=True, text=True)
    if run.returncode or '=' not in run.stdout:
        raise SystemExit('无法读取音频数据：' + run.stderr.strip()[-300:])
    return run.stdout.strip().split('=', 1)[1]


def rewrite(audio_path, backup_dir, change, verify, skip_title=False):
    """Apply `change(song)` to a copy, check with `verify(song)` and that nothing else changed,
    then swap the copy in."""
    audio_path = Path(audio_path)
    original = audio_path.read_bytes()
    before = Song(audio_path)
    before_audio, before_others = audio_hash(audio_path), before.others(skip_title)
    backup = Path(backup_dir) / (sha(original) + '.original' + audio_path.suffix.lower())
    backup.parent.mkdir(parents=True, exist_ok=True)
    if backup.exists():
        if sha(backup.read_bytes()) != sha(original):
            raise SystemExit('Invalid backup')
    else:
        backup.write_bytes(original)
    # no audio extension, so a player watching the folder does not pick the copy up as a song
    temporary = audio_path.with_name(audio_path.name + '.' + secrets.token_hex(8) + '.tmp')
    try:
        shutil.copyfile(audio_path, temporary)
        song = Song(temporary, before.opener)
        change(song)
        song.save()
        after = Song(temporary, before.opener)
        if audio_hash(temporary) != before_audio or getattr(after.audio.info, 'md5_signature', None) != getattr(before.audio.info, 'md5_signature', None):
            raise SystemExit('Audio data changed; original left untouched')
        if after.others(skip_title) != before_others:
            raise SystemExit('Other tags or the cover changed; original left untouched')
        if not verify(after):
            raise SystemExit('The change did not read back as written; original left untouched')
        if sha(audio_path.read_bytes()) != sha(original):
            raise SystemExit('Original changed before commit')
        updated = temporary.read_bytes()
        os.replace(temporary, audio_path)
    finally:
        if temporary.exists():
            temporary.unlink()
    if sha(audio_path.read_bytes()) != sha(updated):
        raise SystemExit('Written checksum mismatch')
    return {'audioPath': str(audio_path), 'backupPath': str(backup), 'sourceSha256': sha(original), 'outputSha256': sha(updated),
            'audioTailSha256': before_audio, 'audioBytesUnchanged': True, 'nonLyricFramesUnchanged': True, 'tagKind': before.kind}


def main():
    audio_path, plain_path, ruby_path, backup_dir = sys.argv[1:5]
    language = sys.argv[5] if len(sys.argv) > 5 else 'und'
    plain = Path(plain_path).read_text(encoding='utf-8')
    ruby = None if ruby_path == '-' else Path(ruby_path).read_text(encoding='utf-8')
    report = rewrite(audio_path, backup_dir, lambda song: song.set_lyrics(plain, ruby, language), lambda song: song.lyrics() == (plain, ruby))
    report['versions'] = [{'language': '', 'descriptor': name, 'characters': len(text)} for name, text in (('', plain), (RUBY, ruby)) if text]
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
