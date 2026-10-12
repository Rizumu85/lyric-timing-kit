"""Time a whole song in one alignment pass, combining three readings of it.

  1. The karaoke model NOT told the lyrics: its most likely sound every 20 ms. Wherever a stretch
     of the lyrics is found in this reading, those sounds are pinned to the moment they were heard.
     The song is read this way several times over: by the karaoke model on the separated vocals
     and on the full mix (separation can remove what the mix still has, and the other way round),
     and by a second model trained on other recordings (RomajiASR, Japanese singing). A sound one
     reading missed is taken from another; where readings put the same sound far apart, two that
     agree win, and otherwise none is believed.
  2. The line times that came with the lyrics (source.json): say which stretch of the recording a
     line belongs to, so a repeated line is matched to the right occurrence, and bound the lines
     that were not heard.
  3. The karaoke model told the lyrics: one pass over the whole song that fills in every sound
     the first reading missed, between the pinned ones. A sound is used once, so a line cannot
     begin inside the previous line's tail.

Lines the user placed by hand stay exactly where they are. Only drafts are written: the song goes
to the listening page; the music file is not touched.

batch_align.py runs this on every Japanese song it aligns. Run by hand it re-times songs that
were aligned before:
  python tools/onepass.py <key> ...             preview
  python tools/onepass.py <key> ... --write     write the drafts (the songs go to the listening page)
  python tools/onepass.py --eval-hand [<key> ...]   where would the tool now put the lines the user placed by hand? (read-only)
  --separate   also let both models hear the vocals separated by BS-RoFormer (about a minute more a song)
  --no-second  the pipeline's own reading only
"""
import json
import re
import shutil
import sys
from pathlib import Path

import numpy as np
import torch
from numba import njit

from kit_config import OUT, MODELS, FA_KARA

MODEL = 'NextFire/mms-300m-ForcedAligner-karaoke-ja-Latn'
STEP = .02  # seconds a frame of the karaoke model
_tokenizer = None


def tokenizer():
    global _tokenizer
    if _tokenizer is None:
        from huggingface_hub import snapshot_download
        from transformers import Wav2Vec2CTCTokenizer
        _tokenizer = Wav2Vec2CTCTokenizer.from_pretrained(snapshot_download(MODEL, local_files_only=True))
    return _tokenizer


def read(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def leaves(line):
    """The timed units of a line, in order: the readings of a syllable that has them, else the syllable."""
    out = []
    for word in line.get('words') or []:
        for unit in word.get('syllables') or [word]:
            out.extend(unit.get('ruby') or [unit])
    return out


def sung(prepared):
    """Per lyric row, the pronunciations the aligner was given (one per timed unit)."""
    rows, group = [], []
    for item in prepared['items']:
        if item['orig'] == '\n':
            rows.append([unit['pron'] for unit in group if unit.get('pron')])
            group = []
        else:
            group.append(item)
    return rows


def apply(lines, units, times, only=None):
    """Write the new times into the line objects (all lines, or only those in `only`)."""
    for row, (line, group, row_times) in enumerate(zip(lines, units, times)):
        if not group or (only is not None and row not in only):
            continue
        for unit, (start, end) in zip(group, row_times):
            unit['startTime'], unit['endTime'] = start, end
        for word in line['words']:
            parts = word.get('syllables') or [word]
            for part in parts:
                if part.get('ruby'):
                    part['startTime'], part['endTime'] = part['ruby'][0]['startTime'], part['ruby'][-1]['endTime']
            if word.get('syllables'):
                word['startTime'], word['endTime'] = parts[0]['startTime'], parts[-1]['endTime']
        line['startTime'], line['endTime'] = line['words'][0]['startTime'], max(line['words'][-1]['endTime'], line['words'][0]['startTime'])


REACH = 2.0  # a line is looked for this far before its platform time and after the next line's
RUN = 3  # sounds found in a row before they count as heard
ROOM, PER_UNIT = 6.0, .5  # seconds a line may take after its platform time: at least ROOM, or PER_UNIT per unit
SLOW = 12.0  # ... and for the second reader's sound-by-sound matching
EARLY = .25  # a line whose opening was not heard may begin this long before its expected start
LATE = .5  # ... and must begin within this long after it
SHOWN = .2  # a line's last unit is given this long before the next line, when the unit before it can spare it
DOUBT = 1.0  # a line this far from its expected start is pointed out
FAR = 1.0  # in a confirmed song, a line this far from the platform pattern keeps its place
LEAST = .5  # a re-timing that heard a smaller share of the song than this is not used
LEAST_OTHER = .1  # ... for a song that is not Japanese: the multilingual model hears a third to a half of singing
TRUSTED = 1.5  # in such a song what was heard of a line is only believed this close (seconds) to where the line is expected
FOLLOWED = .5  # in such a song a line the model did not make out is only pointed out when it followed the song this well
BACKUP = 'aligned.in-file.fia'  # the version in the music file, kept while a new draft waits to be heard
SECOND = MODELS / 'asr-models' / 'RomajiASR-v1.0.0'  # the second lyric-free reader (weights from its release page; see setup.md)
LAG = 1  # frames by which the second reader puts a sound later than the karaoke model, typically
CLASH = 10  # frames: the two readers this far apart on a sound cancel each other


@njit(cache=True)
def away(at, due, steep):
    """What it costs to pair a lyric sound due at frame `due` with one heard at frame `at`
    (a miss costs 1000). Gentle: only tells equal pairings apart. Steep: free within half a
    second, then 8 a frame, so that a few sounds heard seconds away from where the line has got
    to (the same words sung again later) are not worth more than a couple of misses."""
    if due < 0:
        return 0
    gap = abs(at - due)
    if steep:
        return min(999, max(0, gap - 25) * 8)
    return min(999, gap // 5)


@njit(cache=True)
def pair(lyric, heard, low, high, due, heard_at, steep):
    """Whole lyric against the whole lyric-free reading, in order; lyric sound i may only be
    paired with heard sounds low[i]..high[i]. Among pairings with as few misses as possible, the
    one whose sounds lie nearest to where their line is due (due[i], in frames; -1 = unknown) is
    taken, so that words sung again later are not mistaken for the line. A lyric sound left
    without a partner costs 1000. A heard sound left without one costs the same when `steep` is
    off; with it only 300: singing that is not in the lyrics is common, and pairing a line's
    last sounds with a later repeat must not pay for itself by using up heard sounds. Returns,
    per lyric sound, the heard sound it equals (or -1)."""
    m, n = len(lyric), len(heard)
    miss = 1000
    spare = 300 if steep else 1000
    cost = np.empty((m + 1, n + 1), dtype=np.int32)
    for j in range(n + 1):
        cost[0, j] = j * spare
    for i in range(1, m + 1):
        cost[i, 0] = i * miss
        for j in range(1, n + 1):
            best = min(cost[i - 1, j] + miss, cost[i, j - 1] + spare)
            if low[i - 1] <= j - 1 <= high[i - 1]:
                if lyric[i - 1] == heard[j - 1]:
                    step = cost[i - 1, j - 1] + away(heard_at[j - 1], due[i - 1], steep)
                else:
                    step = cost[i - 1, j - 1] + miss + spare
                if step < best:
                    best = step
            cost[i, j] = best
    out = np.full(m, -1, dtype=np.int64)
    i, j = m, n
    while i > 0 and j > 0:
        inside = low[i - 1] <= j - 1 <= high[i - 1]
        if inside and lyric[i - 1] == heard[j - 1] and cost[i, j] == cost[i - 1, j - 1] + away(heard_at[j - 1], due[i - 1], steep):
            out[i - 1] = j - 1
            i -= 1; j -= 1
        elif inside and lyric[i - 1] != heard[j - 1] and cost[i, j] == cost[i - 1, j - 1] + miss + spare:
            i -= 1; j -= 1
        elif cost[i, j] == cost[i - 1, j] + miss:
            i -= 1
        else:
            j -= 1
    return out


FAMILY = {'k': 1, 'g': 1, 'q': 1, 's': 2, 'z': 2, 'j': 2, 'c': 2, 't': 3, 'd': 3, 'n': 4, 'h': 5, 'f': 5, 'b': 6, 'p': 6, 'v': 6,
          'm': 7, 'y': 8, 'r': 9, 'l': 9, 'w': 10}


def sound_of(text):
    """A unit's pronunciation or a sound named by the second reader as (family of its first
    consonant, vowel): 'zu', 'tsu' and 'ju' are (2, u); 'to' and 'da' share the family 3.
    0 = no consonant, 5 = no vowel."""
    text = ''.join(c for c in text.lower() if 'a' <= c <= 'z')
    vowel = next((i for i, c in enumerate(text) if c in 'aiueo'), len(text))
    onset = text[:vowel]
    family = 2 if onset[:2] in ('ts', 'ch', 'sh') else FAMILY.get(onset[:1], 0)
    return family, ('aiueo'.index(text[vowel]) if vowel < len(text) else 5)


@njit(cache=True)
def pair_sounds(family, vowel, heard_family, heard_vowel, low, high, due, heard_at):
    """Lyric units against the sounds the second reader names, in order, each unit only with
    sounds low[i]..high[i]. The same sound pairs for nothing; a sound of the same family with
    another vowel, or the same vowel after another consonant, pairs at a price: the reader often
    names what it hears a little differently (zu as tsu, to as ta, English as katakana), and its
    count and rhythm are worth having all the same. Returns, per unit, the sound it is paired
    with (or -1)."""
    m, n = len(family), len(heard_family)
    miss, spare, kin = 1000, 300, 350
    cost = np.empty((m + 1, n + 1), dtype=np.int32)
    how = np.zeros((m + 1, n + 1), dtype=np.int8)
    for j in range(n + 1):
        cost[0, j] = j * spare
    for i in range(1, m + 1):
        cost[i, 0] = i * miss
        for j in range(1, n + 1):
            best, way = cost[i - 1, j] + miss, 1
            if cost[i, j - 1] + spare < best:
                best, way = cost[i, j - 1] + spare, 2
            if low[i - 1] <= j - 1 <= high[i - 1]:
                same_family = family[i - 1] == heard_family[j - 1]
                same_vowel = vowel[i - 1] == heard_vowel[j - 1]
                if same_family or same_vowel:
                    step = cost[i - 1, j - 1] + away(heard_at[j - 1], due[i - 1], False) + (0 if same_family and same_vowel else kin)
                    if step < best:
                        best, way = step, 3
            cost[i, j] = best
            how[i, j] = way
    out = np.full(m, -1, dtype=np.int64)
    i, j = m, n
    while i > 0 and j > 0:
        if how[i, j] == 3:
            out[i - 1] = j - 1
            i -= 1; j -= 1
        elif how[i, j] == 1:
            i -= 1
        else:
            j -= 1
    return out


@njit(cache=True)
def viterbi(emit, quiet, same, enter_low, enter_high):
    """CTC alignment of the whole song. emit[t, k]: log-probability of sound k at frame t;
    quiet[t]: of silence; same[k]: sound k is the same as sound k-1. Sound k may only BEGIN at
    frames enter_low[k] <= t < enter_high[k]; it may last as long as it likes. Returns the first
    frame of every sound (-1 everywhere when nothing fits)."""
    frames, count = emit.shape
    states = 2 * count + 1
    low = -1e30
    score = np.full(states, low)
    back = np.zeros((frames, states), dtype=np.int8)
    score[0] = quiet[0]
    if enter_low[0] <= 0 < enter_high[0]:
        score[1] = emit[0, 0]
    new = np.empty(states)
    for t in range(1, frames):
        for s in range(states):
            best, choice = score[s], 0
            if s % 2 == 0:
                if s and score[s - 1] > best:
                    best, choice = score[s - 1], 1
                new[s] = best + quiet[t] if best > low else low
            else:
                k = s // 2
                if enter_low[k] <= t < enter_high[k]:
                    if score[s - 1] > best:
                        best, choice = score[s - 1], 1
                    if s >= 3 and not same[k] and score[s - 2] > best:
                        best, choice = score[s - 2], 2
                new[s] = best + emit[t, k] if best > low else low
            back[t, s] = choice
        for s in range(states):
            score[s] = new[s]
    first = np.full(count, -1, dtype=np.int64)
    state = states - 1 if score[states - 1] >= score[states - 2] else states - 2
    if score[state] <= low:
        return first
    for t in range(frames - 1, -1, -1):
        if state % 2:
            first[state // 2] = t
        state -= back[t, state]
    return first


class Letters:
    """The alphabet of the multilingual model (torchaudio's MMS_FA), with the few calls plan()
    makes on the karaoke model's tokenizer. For the songs that are not Japanese."""

    def __init__(self):
        import torchaudio
        self.table = {letter: index for letter, index in torchaudio.pipelines.MMS_FA.get_dict().items() if letter != '*'}
        self.names = {index: letter for letter, index in self.table.items()}
        self.pad_token_id, self.vocab_size = 0, len(self.table)

    def encode(self, text, add_special_tokens=False):
        return [self.table[letter] for letter in text if letter in self.table and self.table[letter]]

    def convert_ids_to_tokens(self, index):
        return self.names[index]


def letter_sound(letter, before, pinyin):
    """The sound family of a letter (0: not a sound of its own). The multilingual model writes
    what it hears, not how the language spells it: credit comes out as krede, symphony as
    simpuni, qing as jing, xiang as sang. So the lyrics are compared with its reading by family:
    every vowel is one sound, b p f v are one, d t are one, l r are one, the hissing sounds are
    one; y, w and the second letter of sh zh ch ng are no sounds of their own. `before` is the
    letter just before it in the same word ('' at its start). `pinyin`: the letters spell a
    Chinese syllable (q x c hiss); otherwise they are written as heard (the model's reading, or
    an English word respelled by said_as)."""
    if letter in 'aeiou':
        return 1
    if letter in "yw'" or (letter == 'h' and before in ('s', 'z', 'c')) or (letter == 'g' and before == 'n'):
        return 0
    if letter in 'qx':
        return 7 if pinyin else 6
    for family, members in ((2, 'bpfv'), (3, 'm'), (4, 'n'), (5, 'lr'), (6, 'gk'), (7, 'szjc'), (8, 'dt'), (9, 'h')):
        if letter in members:
            return family
    return 10


_said = {}


def said_as(word):
    """An English word written the way it is said (they -> zei, credit -> kredat), from the
    pronouncing dictionary FA-Kara brings; '' when it has no entry."""
    if word not in _said:
        import contextlib
        import io
        if str(FA_KARA) not in sys.path:
            sys.path.insert(0, str(FA_KARA))
        import haruraw2norm
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                parts = haruraw2norm.process_haruhi_line(word, 'jaen')
            _said[word] = ''.join(c for part in parts for c in (part.get('pron') or '').lower() if c.isalpha())
        except Exception:
            _said[word] = ''
    return _said[word]


def reading(emission, tok):
    """What the model hears when it is not told the lyrics: (token ids, first frame of each)."""
    best = emission.argmax(1)
    keep = np.flatnonzero((best != tok.pad_token_id) & np.concatenate(([True], best[1:] != best[:-1])))
    return best[keep].astype(np.int64), keep


_second = None
_first = None


def mix_of(source, rate=16000):
    import subprocess
    run = subprocess.run(['ffmpeg', '-v', 'error', '-i', source['audio'], '-map', '0:a:0', '-ac', '1', '-ar', str(rate), '-f', 'f32le', '-'], capture_output=True)
    return np.frombuffer(run.stdout, dtype=np.float32).copy()


def karaoke_sounds(wave, tok):
    """The karaoke model's most likely sounds in `wave` (mono, 16 kHz): [[token id, frame], ...];
    None when the model is not at hand."""
    global _first
    try:
        from huggingface_hub import snapshot_download
        from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor
        if _first is None:
            path = snapshot_download(MODEL, local_files_only=True)
            _first = (Wav2Vec2Processor.from_pretrained(path), Wav2Vec2ForCTC.from_pretrained(path).to('cuda').eval())
    except Exception:
        return None
    processor, model = _first
    rate, hop, core, edge = 16000, 320, 24, 1
    best = []
    for start in range(0, len(wave), core * rate):
        left, right = max(0, start - edge * rate), min(len(wave), start + (core + edge) * rate)
        with torch.no_grad():
            values = processor(audio=wave[left:right], sampling_rate=rate, return_tensors='pt').input_values.to('cuda')
            frames = model(values).logits[0].argmax(-1).cpu().numpy()
        first = (start - left) // hop
        best.append(frames[first:first + min(core * rate, len(wave) - start) // hop])
    best = np.concatenate(best)
    keep = np.flatnonzero((best != tok.pad_token_id) & np.concatenate(([True], best[1:] != best[:-1])))
    return [[int(best[f]), int(f)] for f in keep]


_multi = None


def multi_sounds(wave):
    """The multilingual model's most likely letters in `wave` (mono, 16 kHz): [[letter id, frame],
    ...]; None when it cannot be loaded. (For the songs that are not Japanese; the pipeline's own
    reading of such a song is the emission it saved, of the separated vocals.)"""
    global _multi
    try:
        if _multi is None:
            import torchaudio
            _multi = torchaudio.pipelines.MMS_FA.get_model(with_star=False).to('cuda').eval()
    except Exception:
        return None
    rate, hop, core, edge = 16000, 320, 24, 1
    best = []
    for start in range(0, len(wave), core * rate):
        left, right = max(0, start - edge * rate), min(len(wave), start + (core + edge) * rate)
        with torch.inference_mode():
            logits, _ = _multi(torch.from_numpy(wave[left:right])[None].to('cuda'))
            frames = logits[0, :, :28].argmax(-1).cpu().numpy()  # (without the "any sound" column, if the model has it)
        first = (start - left) // hop
        best.append(frames[first:first + min(core * rate, len(wave) - start) // hop])
    best = np.concatenate(best)
    keep = np.flatnonzero((best != 0) & np.concatenate(([True], best[1:] != best[:-1])))
    return [[int(best[f]), int(f)] for f in keep]


def second_sounds(wave):
    """The second model's sounds in `wave` (mono, 16 kHz): [[sound, frame], ...]; None when it
    is not installed."""
    global _second
    if not SECOND.exists():
        return None
    from transformers import HubertForCTC
    if _second is None:
        vocab = {index: token for token, index in json.loads((SECOND / 'phoneme_vocab.json').read_text(encoding='utf-8')).items()}
        _second = (HubertForCTC.from_pretrained(str(SECOND)).to('cuda').eval(), vocab)
    model, vocab = _second
    rate, hop, core, edge = 16000, 320, 20, 2
    best = []
    for start in range(0, len(wave), core * rate):
        left, right = max(0, start - edge * rate), min(len(wave), start + (core + edge) * rate)
        with torch.no_grad():
            frames = model(torch.from_numpy(wave[left:right])[None].to('cuda')).logits[0].argmax(-1).cpu().numpy()
        first = (start - left) // hop
        best.append(frames[first:first + min(core * rate, len(wave) - start) // hop])
    best = np.concatenate(best)
    keep = np.flatnonzero((best != 0) & np.concatenate(([True], best[1:] != best[:-1])))
    skip = {'<blank>', '<unk>', 'SOS', 'EOS', 'cl'}
    return [[vocab[int(best[f])].lower(), int(f)] for f in keep if vocab[int(best[f])] not in skip]


def as_tokens(sounds):
    if not sounds:
        return None
    sounds = np.asarray(sounds, dtype=np.int64)
    return sounds[:, 0], sounds[:, 1]


def as_letters(sounds, tok):
    """The second model's sounds as the karaoke model's token ids: every letter of a sound gets
    the frame at which the sound began."""
    if not sounds:
        return None
    letters = {tok.convert_ids_to_tokens(index).lower(): index for index in range(tok.vocab_size)}
    ids, at = [], []
    for text, frame in sounds:
        for letter in text:
            if letter in letters:
                ids.append(letters[letter]); at.append(frame)
    return np.asarray(ids, dtype=np.int64), np.asarray(at, dtype=np.int64)


def mix_reading(folder, source, tok, japanese=True):
    """What the karaoke model (for a song that is not Japanese: the multilingual model) hears in
    the full mix (the pipeline's own reading is of the separated vocals), as (token ids, frame of
    each). Kept next to the draft as mix-reading.json; None when the model is not at hand."""
    cache = folder / 'mix-reading.json'
    sounds = read(cache)
    if sounds is None:
        wave = mix_of(source)
        sounds = (karaoke_sounds(wave, tok) if japanese else multi_sounds(wave)) if len(wave) else None
        if sounds is None:
            return None
        cache.write_text(json.dumps(sounds), encoding='utf-8')
    return as_tokens(sounds)


def second_reading(folder, source, tok):
    """What the second model hears in the full mix, as (the karaoke model's token ids, frame of
    each). Kept next to the draft as second-reading.json; None when the model is not installed."""
    cache = folder / 'second-reading.json'
    sounds = read(cache)
    if sounds is None:
        wave = mix_of(source)
        sounds = second_sounds(wave) if len(wave) else None
        if sounds is None:
            return None
        cache.write_text(json.dumps(sounds), encoding='utf-8')
    return as_letters(sounds, tok)


def vocals_readings(folder, source, tok, separate, japanese=True):
    """What both models hear in the vocals separated by BS-RoFormer (the second model hears most
    in this version of a song): [karaoke reading, second reading], each as in mix_reading. Kept
    next to the draft as vocals-readings.json. Separating takes about a minute a song, so it is
    only done when `separate` is set; otherwise [] unless the file is there."""
    cache = folder / 'vocals-readings.json'
    kept = read(cache)
    if kept is None:
        if not separate:
            return []
        import subprocess
        import tempfile
        from pathlib import Path
        from audio_separator.separator import Separator
        work = Path(tempfile.mkdtemp(prefix='sep-'))
        # (half precision on the GPU: about half the time; the models then hear the same but for
        # one sound in some hundreds, and no final time changed on the songs it was tried on)
        separator = Separator(output_dir=str(work), model_file_dir=str(MODELS / 'sep-models'), output_single_stem='Vocals', use_autocast=True)
        separator.load_model('model_bs_roformer_ep_317_sdr_12.9755.ckpt')
        files = separator.separate(source['audio'])
        run = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(work / Path(files[0]).name), '-ac', '1', '-ar', '16000', '-f', 'f32le', '-'], capture_output=True)
        wave = np.frombuffer(run.stdout, dtype=np.float32).copy()
        del separator
        torch.cuda.empty_cache()
        shutil.rmtree(work, ignore_errors=True)
        if not len(wave):
            return []
        kept = {'karaoke': karaoke_sounds(wave, tok) or [], 'second': second_sounds(wave) or []} if japanese else {'multi': multi_sounds(wave) or []}
        cache.write_text(json.dumps(kept), encoding='utf-8')
    out = []
    if kept.get('karaoke') or kept.get('multi'):  # (the same model as the first reading, on other audio)
        out.append(('karaoke', as_tokens(kept.get('karaoke') or kept['multi'])))
    if kept.get('second'):
        out.append(('second', as_letters(kept['second'], tok)))
    return out


_english = None


def is_english(text):
    """A line written in Latin letters only (an English line in a Japanese song)."""
    letters = [c for c in text if c.isalpha()]
    return len(letters) >= 3 and all(c.isascii() for c in letters)


def english_times(wave, words):
    """Where each of `words` is sung in `wave` (mono, 16 kHz), from the multilingual aligner the
    pipeline uses for songs that are not Japanese (it knows English spelling; the karaoke model
    and the second reader only hear English as if it were Japanese). Returns [(start, end)] in
    seconds from the beginning of `wave`, None for words without letters; None if it cannot."""
    global _english
    import torchaudio
    try:
        if _english is None:
            bundle = torchaudio.pipelines.MMS_FA
            _english = (bundle.get_model(with_star=False).to('cuda').eval(), bundle.get_tokenizer(), bundle.get_aligner())
        model, tokenizer, aligner = _english
        spelled = [''.join(c for c in word.lower() if 'a' <= c <= 'z' or c == "'").strip("'") for word in words]
        sung = [word for word in spelled if word]
        if not sung or len(wave) < 3200:
            return None
        with torch.inference_mode():
            emission, _ = model(torch.from_numpy(wave)[None].to('cuda'))
            spans = aligner(emission[0], tokenizer(sung))
        seconds = len(wave) / emission.size(1) / 16000
    except Exception:
        return None
    out, found = [], iter(spans)
    for word in spelled:
        if not word:
            out.append(None)
            continue
        span = next(found)
        out.append((span[0].start * seconds, span[-1].end * seconds))
    return out


def clock(t):
    return f'{int(t // 60)}:{t % 60:05.2f}'


def plan(key, pins=True, second=True, separate=False, keep=True, hold=()):
    """The new times of one song. Returns (doc, report); report['lines'] has one entry per sung
    line: line number, old and new start, expected start, share of its sounds heard, flags."""
    folder = OUT / key
    current = read(folder / 'aligned.fia')
    # the version in the music file: the kept copy while a draft is waiting to be heard, otherwise
    # the draft itself (a kept copy is out of date once the song has been written again)
    waiting = (read(folder / 'result.json') or {}).get('status') != 'complete'
    base = (read(folder / BACKUP) if waiting else None) or current
    prepared, source = read(folder / 'prepared.json'), read(folder / 'source.json')
    japanese = (source or {}).get('language') == 'ja'
    sound = folder / ('ja-emission.pt' if japanese else 'mms-emission.pt')
    if not base or not prepared or not source or not sound.exists():
        raise ValueError('draft, pronunciations, lyric sheet or emission missing')
    prons = sung(prepared)
    sheet = [row.get('sourceStart') for row in source['rows']]

    def in_step(candidate):
        rows = candidate['lyrics']['lines']
        return len(prons) == len(rows) == len(sheet) and all(len(leaves(line)) == len(p) for line, p in zip(rows, prons))

    if not in_step(base):
        # readings corrected since the music file was written: the current draft has the right units
        if not in_step(current):
            raise ValueError('draft, pronunciations and lyric sheet are out of step')
        base = current
        keep = keep and not waiting  # (a draft that is waiting has not been heard yet)
    doc = json.loads(json.dumps(base))
    lines = doc['lyrics']['lines']
    units = [leaves(line) for line in lines]
    # A song the user has confirmed: every line of the version in the music file has been heard.
    # A line that sits far from where the platform times would put it was moved there (by hand,
    # at a note of the user's, or by the first alignment) and then approved. Its platform time is
    # wrong; pulling the line back to it would undo a correction. So for such a line the place
    # it has stands in for the platform time. (The first alignment put line starts up to 0.6 s
    # early, which is why smaller differences are not taken as corrections.) A line the platform
    # gives no time for takes its place in the same way.
    kept_place = set()
    if keep and (read(folder / 'result.json') or {}).get('confirmed'):
        gaps = [units[i][0]['startTime'] - sheet[i] for i in range(len(lines)) if units[i] and sheet[i] is not None]
        if len(gaps) >= 5:
            usual = float(np.median(gaps))
            for i in range(len(lines)):
                if units[i] and (sheet[i] is None or abs(units[i][0]['startTime'] - sheet[i] - usual) > FAR):
                    sheet[i] = round(units[i][0]['startTime'] - usual, 2)
                    kept_place.add(i)
    emission = torch.load(sound, map_location='cpu', weights_only=True).float().numpy()
    if not japanese:
        # the multilingual model's last column is its "any sound" token, which was given half of
        # every frame when the emission was saved: it is left out and the rest made to sum to one
        emission = emission[:, :-1]
        emission = emission - np.log(np.exp(emission).sum(1, keepdims=True))
    total = len(emission)
    tok = tokenizer() if japanese else Letters()
    blank = tok.pad_token_id
    order = [i for i in range(len(lines)) if units[i] and prons[i]]
    tokens, owner = [], []  # owner: (line, unit, index of the sound inside the unit)
    for i in order:
        for position, pron in enumerate(prons[i]):
            for k, token in enumerate(tok.encode(pron, add_special_tokens=False)):
                tokens.append(token); owner.append((i, position, k))
    tokens = np.asarray(tokens, dtype=np.int64)
    line_of = np.asarray([o[0] for o in owner])
    opening = {i: int(np.flatnonzero(line_of == i)[0]) for i in order}
    leading = np.asarray([o[2] == 0 for o in owner])  # the first sound of a unit
    unit_of = np.cumsum(leading) - 1
    heard, heard_at = reading(emission, tok)
    # What counts as the same sound when the lyrics are compared with a reading: for the karaoke
    # model the letter itself (it writes Japanese the way the lyrics are romanized); for the
    # multilingual model the letter's family (see letter_sound).
    def by_family(ids, at):
        """A reading of the multilingual model as sound families (see letter_sound)."""
        got, got_at = [], []
        for k in range(len(ids)):
            near = k and at[k] - at[k - 1] <= 10
            family = letter_sound(tok.names[int(ids[k])], tok.names[int(ids[k - 1])] if near else '', False)
            if family and not (got and family == got[-1] and at[k] - got_at[-1] <= 15):
                got.append(family); got_at.append(int(at[k]))
        return np.asarray(got, dtype=np.int64), np.asarray(got_at, dtype=np.int64)

    sound, silent = tokens, np.zeros(len(tokens), dtype=bool)
    if not japanese:
        sound = np.zeros(len(tokens), dtype=np.int64)
        k = 0
        while k < len(tokens):
            n = k
            while n < len(tokens) and owner[n][:2] == owner[k][:2]:
                n += 1
            i, position = owner[k][:2]
            text = units[i][position].get('text') or ''
            if re.search('[\u3400-\u9fff]', text):  # a Chinese character: its letters are pinyin
                spelled = [tok.names[int(t)] for t in tokens[k:n]]
                families = [letter_sound(letter, spelled[j - 1] if j else '', True) for j, letter in enumerate(spelled)]
            else:  # a word: compared the way it is said, one sound to a letter as far as they go
                said = said_as(text.strip()) or ''.join(tok.names[int(t)] for t in tokens[k:n])
                families = [f for f in (letter_sound(letter, said[j - 1] if j else '', False) for j, letter in enumerate(said)) if f][:n - k]
                families += [0] * (n - k - len(families))
            sound[k:n] = families
            k = n
        silent = sound == 0
        heard, heard_at = by_family(heard, heard_at)

    # lines placed by hand: in the version in the music file, and on the draft now on the listening page
    pinned = {}
    if pins:
        # where the user put them is read off the current draft (the kept copy of the music file's
        # version may be older than the user's latest adjustments)
        now = current['lyrics']['lines'] if len(current['lyrics']['lines']) == len(lines) else lines
        for index in ((read(folder / 'timing-edits.applied.json') or {}).get('l') or {}):
            if int(index) in opening and leaves(now[int(index)]):
                pinned[int(index)] = leaves(now[int(index)])[0]['startTime']
        # ... and the adjustments waiting on the draft now on the listening page
        edits = read(folder / 'timing-edits.json') or {}
        for index, edit in (edits.get('l') or {}).items():
            line = current['lyrics']['lines'][int(index)]
            if edit.get('t') == line.get('fullText') and int(index) in opening and (edit.get('d') or edits.get('g')):
                pinned[int(index)] = round(leaves(line)[0]['startTime'] + (edits.get('g') or 0) + (edit.get('d') or 0), 2)

    # lines whose start is to stay where the draft has it (`hold`): only their words are timed again
    for index in hold:
        if index in opening and index not in pinned:
            pinned[index] = units[index][0]['startTime']

    # which stretch of the recording each line may lie in: from the platform times, and for a
    # line placed by hand from where the user put it (that holds whatever the platform says)
    length = total * STEP

    def stretch(i, offset, reach, room=ROOM):
        known = [(j, pinned[j] if j in pinned else sheet[j] + offset) for j in order if j in pinned or sheet[j] is not None]
        before = max((t for j, t in known if j <= i), default=0.0)
        after = min((t for j, t in known if j > i and t > before + .05), default=length)
        # a line before an interlude, or the last line, is not looked for all the way to the next
        # one: the same words sung again later (an echo, the outro) are not this line
        after = min(after, before + max(room, PER_UNIT * len(units[i])))
        return before - (.5 if i in pinned else reach), after + reach

    # A doubled letter (a long vowel, a doubled consonant) is heard as one sound: for the matching
    # only one of the pair is kept, the one that opens a unit if either does.
    single = ~silent
    for k in range(1, len(tokens)) if japanese else ():
        if tokens[k] == tokens[k - 1] and line_of[k] == line_of[k - 1] and single[k - 1]:
            if leading[k] and not leading[k - 1]:
                single[k - 1] = False
            else:
                single[k] = False
    last = -1  # (other languages: the last sound kept, which need not be the letter just before)
    for k in range(len(tokens)) if not japanese else ():
        if not single[k]:
            continue
        if last >= 0 and sound[k] == sound[last] and line_of[k] == line_of[last]:
            if leading[k] and not leading[last]:
                single[last] = False; last = k
            else:
                single[k] = False
        else:
            last = k
    place = np.cumsum(single) - 1  # position of every sound among those kept
    in_line = place - np.asarray([place[opening[i]] for i in line_of])

    def match(offset, reach, heard=heard, heard_at=heard_at, pace=None):
        """Per lyric sound, the frame at which this reading heard it (-1: not). With `pace`
        (frames a sound) every sound is due where its line has got to at that pace."""
        low, high = np.empty(len(tokens), dtype=np.int64), np.empty(len(tokens), dtype=np.int64)
        for i in order:
            a, b = stretch(i, offset, reach)
            own = line_of == i
            low[own], high[own] = np.searchsorted(heard_at * STEP, a), np.searchsorted(heard_at * STEP, b) - 1
        due = np.full(len(tokens), -1, dtype=np.int64)
        for i in order:
            if i in pinned or sheet[i] is not None:
                own = line_of == i
                begin = pinned[i] if i in pinned else sheet[i] + offset
                due[own] = int(begin / STEP) + (0 if pace is None else (in_line[own] * pace).astype(np.int64))
        kept = np.flatnonzero(single)
        found = np.full(len(tokens), -1, dtype=np.int64)
        found[kept] = pair(sound[kept], heard, low[kept], high[kept], due[kept], heard_at.astype(np.int64), pace is not None)
        # only sounds found in a row count, and they must have been heard close together
        solid = np.zeros(len(tokens), dtype=bool)
        start = 0
        for n in range(1, len(kept) + 1):
            broken = n == len(kept) or found[kept[n]] < 0 or found[kept[n - 1]] < 0 or heard_at[found[kept[n]]] - heard_at[found[kept[n - 1]]] > .6 / STEP
            if broken:
                if found[kept[start]] >= 0 and n - start >= RUN:
                    solid[kept[start:n]] = True
                start = n
        return np.where(solid, heard_at[np.maximum(found, 0)], -1)

    # how far the song as a whole sits from the platform times: from lines whose opening was heard
    rough = match(0.0, 6.0)
    gaps = [rough[opening[i]] * STEP - sheet[i] for i in order if sheet[i] is not None and rough[opening[i]] >= 0 and i not in pinned]
    if len(gaps) < 5:  # nearly every line placed by hand: where the user put them says how far off the platform is
        gaps = [pinned[i] - sheet[i] for i in pinned if sheet[i] is not None] if len(pinned) >= 5 else gaps + [0.0] * 5
    offset = float(np.median(gaps)) if len(gaps) >= 5 else 0.0
    # ... and how fast it is sung: frames a sound, from the lines heard nearly whole
    paces = []
    for i in order:
        own = np.flatnonzero((line_of == i) & (rough >= 0))
        if len(own) >= 6 and place[own[-1]] > place[own[0]]:
            paces.append((rough[own[-1]] - rough[own[0]]) / (place[own[-1]] - place[own[0]]))
    pace = float(np.median(paces)) if len(paces) >= 5 else None
    anchor = match(offset, REACH, pace=pace)
    gaps = [anchor[opening[i]] * STEP - sheet[i] - offset for i in order if sheet[i] is not None and anchor[opening[i]] >= 0 and i not in pinned]
    spread = float(np.median(np.abs(gaps))) if gaps else 0.0

    def match_sounds(sounds):
        """The second reader's sounds against the units, sound by sound (see pair_sounds): per
        lyric sound the frame of its unit (on the unit's first sound only), or -1."""
        lead = np.flatnonzero(leading)
        spelled = [sound_of(prons[owner[k][0]][owner[k][1]]) for k in lead]
        named = [sound_of(text) for text, frame in sounds]
        at = np.asarray([frame for text, frame in sounds], dtype=np.int64)
        low, high, due = np.empty(len(lead), dtype=np.int64), np.empty(len(lead), dtype=np.int64), np.full(len(lead), -1, dtype=np.int64)
        for i in order:
            a, b = stretch(i, offset, REACH, SLOW)  # sound by sound a slowly sung line can be followed further
            own = line_of[lead] == i
            low[own], high[own] = np.searchsorted(at * STEP, a), np.searchsorted(at * STEP, b) - 1
            if i in pinned or sheet[i] is not None:
                begin = pinned[i] if i in pinned else sheet[i] + offset
                due[own] = int(begin / STEP) + (0 if pace is None else (in_line[lead[own]] * pace).astype(np.int64))
        found = pair_sounds(np.asarray([f for f, v in spelled], dtype=np.int64), np.asarray([v for f, v in spelled], dtype=np.int64),
                            np.asarray([f for f, v in named], dtype=np.int64), np.asarray([v for f, v in named], dtype=np.int64), low, high, due, at)
        # units paired in a row count, with at most one of the reader's sounds skipped between two
        solid = np.zeros(len(lead), dtype=bool)
        start = 0
        for n in range(1, len(lead) + 1):
            if n == len(lead) or found[n] < 0 or found[n - 1] < 0 or found[n] - found[n - 1] > 2 or line_of[lead[n]] - line_of[lead[n - 1]] > 1:
                if found[start] >= 0 and n - start >= RUN:
                    solid[start:n] = True
                start = n
        out = np.full(len(tokens), -1, dtype=np.int64)
        out[lead[solid]] = at[found[solid]] - LAG
        return out

    # The other readings. Only the first sound of a unit takes a time from them (the second
    # model names whole sounds; for the karaoke model on the mix this keeps the vote simple).
    loose = np.zeros(len(tokens), dtype=bool)
    others, readers = [], []
    if second:
        mix = mix_reading(folder, source, tok, japanese)
        if mix is not None and not japanese:
            mix = by_family(*mix)
        if mix is not None and len(mix[0]):
            others.append(match(offset, REACH, *mix, pace=pace)); readers.append('karaoke')
        other = second_reading(folder, source, tok) if japanese else None  # (the second model is Japanese)
        if other is not None and len(other[0]):
            others.append(match_sounds(read(folder / 'second-reading.json'))); readers.append('second')
        for reader, sounds in vocals_readings(folder, source, tok, separate, japanese):
            if not japanese:
                sounds = by_family(*sounds)
            if reader == 'second':
                others.append(match_sounds(read(folder / 'vocals-readings.json')['second']))
            else:
                others.append(match(offset, REACH, *sounds, pace=pace))
            readers.append(reader)
    added = cancelled = overruled = 0
    # the sounds the second reader names, one by one: used to tell where other words begin
    spoken = spoken_at = None
    raw = read(folder / 'second-reading.json') if second else None
    if raw:
        spoken, spoken_at = [text for text, frame in raw], np.asarray([frame for text, frame in raw], dtype=np.int64)

    def fits(k, frame):
        """A sound pinned at `frame` leaves room for the sounds between it and its pinned neighbours."""
        pinned_at = np.flatnonzero((anchor >= 0) & (unit_of != unit_of[k]))
        before, after = pinned_at[pinned_at < k], pinned_at[pinned_at > k]
        return (not len(before) or frame - anchor[before[-1]] >= 2 * (k - before[-1])) and (not len(after) or anchor[after[0]] - frame >= 2 * (after[0] - k))

    # The karaoke model's readings of different versions of a song tend to err together (the same
    # words sung twice, taken for the wrong time), so they do not outvote the other model: where
    # both readings of the second model put a unit at the same place, that place is taken.
    strong = np.zeros(len(tokens), dtype=bool)
    for k in np.flatnonzero(leading) if others else ():
        votes = [int(v[k]) for v in others if v[k] >= 0]
        if not votes:
            continue
        theirs = [int(v[k]) for v, who in zip(others, readers) if who == 'second' and v[k] >= 0]
        agreed = int(np.median(theirs)) if len(theirs) >= 2 and max(theirs) - min(theirs) <= CLASH else -1
        mine = int(anchor[k])
        if mine >= 0:
            if agreed >= 0 and abs(agreed - mine) > CLASH:
                anchor[unit_of == unit_of[k]] = -1
                anchor[k] = agreed; loose[k] = strong[k] = True; overruled += 1
                continue
            far = [v for v in votes if abs(v - mine) > CLASH]
            if not far or len(far) < len(votes):
                continue  # nobody disagrees, or another reading agrees with the pipeline's own
            anchor[unit_of == unit_of[k]] = -1  # the unit is left to the alignment ...
            if len(far) >= 2 and max(far) - min(far) <= CLASH and fits(k, int(np.median(far))):
                anchor[k] = int(np.median(far)); loose[k] = True; overruled += 1  # ... unless two others agree
            else:
                cancelled += 1
        elif agreed >= 0:
            anchor[k] = agreed; loose[k] = strong[k] = True; added += 1
        elif len(votes) == 1 or max(votes) - min(votes) <= CLASH:
            frame = int(np.median(votes))
            if fits(k, frame):
                anchor[k] = frame; loose[k] = True; added += 1
    # what the second model's two readings agree on stands; other pins that do not fit between
    # those are dropped
    sure = np.flatnonzero(strong)
    for k in np.flatnonzero((anchor >= 0) & ~strong) if len(sure) else ():
        before, after = sure[sure < k], sure[sure > k]
        if (len(before) and anchor[k] - anchor[before[-1]] < k - before[-1]) or (len(after) and anchor[after[0]] - anchor[k] < after[0] - k):
            anchor[k] = -1; cancelled += 1

    # A line of which next to nothing was heard where the platform puts it may be somewhere else
    # (a wrong platform time): it is looked for in every reading between what was heard of the
    # line before it and of the line after it, and taken where most of it is heard in one go --
    # but only when two readings find it at the same place: one alone was wrong half the time.
    found_elsewhere = {}
    streams = [(heard, heard_at)] + ([mix] if second and mix is not None and len(mix[0]) else []) + ([other] if second and other is not None and len(other[0]) else [])
    streams += [sounds if japanese else by_family(*sounds) for _, sounds in vocals_readings(folder, source, tok, False, japanese)] if second else []
    # (Not in a song that is not Japanese: all its readings come from one model, and one model
    # reading other audio goes wrong in the same places -- two of them agreeing proves nothing.)
    for i in order if japanese else ():
        own = np.flatnonzero(line_of == i)
        lead = own[leading[own]]
        if len(lead) < 4 or single[own].sum() < 6 or (anchor[lead] >= 0).mean() >= .34:  # (a line of one repeated sound, a vocalise, could be "found" anywhere)
            continue
        before = anchor[:own[0]][anchor[:own[0]] >= 0]
        after = anchor[own[-1] + 1:][anchor[own[-1] + 1:] >= 0]
        left = int(before[-1]) + 1 if len(before) else 0
        right = int(after[0]) - 1 if len(after) else total
        kept = own[single[own]]
        finds = []
        for sounds, sounds_at in streams:
            a, b = np.searchsorted(sounds_at, left), np.searchsorted(sounds_at, right) - 1
            if b - a + 1 < len(kept) // 2:
                continue
            got = pair(sound[kept], sounds, np.full(len(kept), a, dtype=np.int64), np.full(len(kept), b, dtype=np.int64),
                       np.full(len(kept), -1, dtype=np.int64), sounds_at.astype(np.int64), True)
            hit = np.flatnonzero(got >= 0)
            if len(hit) < .6 * len(kept):
                continue
            at = sounds_at[got[hit]]
            if (at[-1] - at[0]) <= max(100, 4 * (pace or 6) * len(kept)):
                finds.append((hit, at))
        agreed = [f for f in finds if any(g is not f and abs(int(g[1][0]) - int(f[1][0])) <= 25 for g in finds)]
        if agreed:
            hit, at = max(agreed, key=lambda f: len(f[0]))
            anchor[own] = -1
            anchor[kept[hit]] = at
            loose[kept[hit]] = True
            found_elsewhere[i] = (int(at[0]), int(at[-1]))

    for i, at in pinned.items():
        frame = int(round(at / STEP))
        anchor[(line_of == i) & (anchor < frame + 1)] = -1
        anchor[(line_of < i) & (anchor >= frame)] = -1
        anchor[opening[i]] = frame
    # pinned sounds must leave room for the sounds between them
    same = np.concatenate(([False], tokens[1:] == tokens[:-1]))
    last_k, last_frame = -1, -1
    for k in np.flatnonzero(anchor >= 0):
        need = (k - last_k) + int(same[last_k + 1:k + 1].sum())
        hand = any(opening[i] == k for i in pinned)
        if anchor[k] - last_frame >= need or last_k < 0 or hand:
            last_k, last_frame = k, anchor[k]
        else:
            anchor[k] = -1

    expected = {i: sheet[i] + offset for i in order if sheet[i] is not None}
    # A song that is not Japanese is read by one model only, and by sound family: the same words
    # sung again a few seconds on are easily taken for the line. What was heard of a line is
    # believed only when it puts the line's start within TRUSTED of where the line is expected;
    # otherwise nothing of the line is pinned and the alignment places it from the platform time.
    for i in order if not japanese else ():
        own = np.flatnonzero((line_of == i) & (anchor >= 0))
        if len(own) and i in expected and i not in pinned:
            first = own[0]
            begin = (anchor[first] - in_line[first] * (pace or 10)) * STEP
            if abs(begin - expected[i]) > TRUSTED:
                anchor[line_of == i] = -1
    hand = {opening[i] for i in pinned}

    unbound = set()  # lines whose platform time is given up because hand-placed neighbours contradict it

    def adrift(i):
        """Nothing near the beginning of line i was heard."""
        own = anchor[line_of == i]
        return own[0] < 0 and not (own[:8] >= 0).any()

    def windows():
        enter_low = np.zeros(len(tokens), dtype=np.int64)
        enter_high = np.full(len(tokens), total, dtype=np.int64)
        late = {}
        for i in order:
            a, b = stretch(i, offset, REACH, SLOW)  # (as far as the sound-by-sound matching may have pinned it)
            own = line_of == i
            enter_low[own], enter_high[own] = max(0, int(a / STEP)), min(total, int(b / STEP) + 1)
            k = opening[i]
            if i in found_elsewhere:  # the platform time does not hold for this line
                enter_low[own], enter_high[own] = max(0, found_elsewhere[i][0] - 100), min(total, found_elsewhere[i][1] + 150)
                continue
            if i in unbound:  # ... nor for this one: only its neighbours hold it
                enter_low[own], enter_high[own] = 0, total
                continue
            if anchor[k] < 0 and i in expected:
                # the opening was not heard: it may not begin long before the line's expected start,
                # unless the rest of the line was heard earlier than that
                heard_later = anchor[own][anchor[own] >= 0]
                limit = int((expected[i] - max(EARLY, 2 * spread)) / STEP)
                if len(heard_later):
                    limit = min(limit, int(heard_later[0]) - 2 * int(np.flatnonzero(anchor[own] >= 0)[0]) - 1)
                enter_low[k] = max(enter_low[k], limit)
                late[k] = int((expected[i] + LATE) / STEP)
        floor = 0
        for k in range(len(tokens)):
            if anchor[k] >= 0:
                exact = k in hand
                slack = 0 if exact else (2 if loose[k] else 1)
                enter_low[k], enter_high[k] = anchor[k] - slack, anchor[k] + slack + 1
                floor = anchor[k]
            else:
                enter_low[k] = max(enter_low[k], floor)
                if k in late:
                    # ... nor long after it: the platform says the line has begun, even though the
                    # model did not make out its first sound (unless the line before is still heard)
                    enter_high[k] = min(enter_high[k], max(late[k], floor + 3))
        ceiling = total
        for k in range(len(tokens) - 1, -1, -1):
            if anchor[k] >= 0:
                ceiling = anchor[k] + 1
            else:
                enter_high[k] = max(min(enter_high[k], ceiling + 1), enter_low[k] + 1)
        return enter_low, enter_high

    # Where the windows leave no room, the sound heard nearest the jam is no longer pinned.
    for _ in range(len(tokens)):
        enter_low, enter_high = windows()
        earliest, jam = -1, -1
        for k in range(len(tokens)):
            earliest = max(int(enter_low[k]), earliest + (2 if same[k] else 1))
            if earliest >= enter_high[k]:
                jam = k
                break
        if jam < 0:
            break
        free = [k for k in np.flatnonzero(anchor >= 0) if k not in hand]
        if not free:
            # Only hand-placed lines are left: where the user has moved lines far from the
            # platform times, a line between them cannot keep to its platform time either.
            lines_near = {int(line_of[jam])} | ({int(line_of[jam - 1])} if jam else set())
            if lines_near <= unbound:
                raise ValueError('the lines placed by hand leave no room for the lines between them')
            unbound |= lines_near
            continue
        anchor[min(free, key=lambda k: abs(k - jam))] = -1
    first = viterbi(np.ascontiguousarray(emission[:, tokens]), np.ascontiguousarray(emission[:, blank]), same, enter_low, enter_high)
    if first[0] < 0:
        raise ValueError('no alignment fits what was heard and the platform times')
    # A sound that was not heard may have been given frames in which the model hardly believes in
    # it. It begins at the first frame where that belief reaches a third of its strongest.
    for k in range(len(tokens)):
        if anchor[k] >= 0 or owner[k][2]:
            continue
        stop_at = int(first[k + 1]) if k + 1 < len(first) else total
        belief = emission[first[k]:max(stop_at, first[k] + 1), tokens[k]]
        first[k] = max(first[k], min(first[k] + int(np.flatnonzero(belief >= belief.max() + np.log(1 / 3))[0]), enter_high[k] - 1))

    # A line of which nothing near the beginning was heard, pressed against the late edge of its
    # window, was placed by the window and not by anything in the sound: the platform time is the
    # better guess.
    for i in order:
        k = opening[i]
        if anchor[k] >= 0 or i in pinned or i not in expected or i in found_elsewhere:
            continue
        if adrift(i) and first[k] >= int((expected[i] + LATE) / STEP) - 3:
            room_low = int(first[k - 1]) + 1 if k else 0
            room_high = int(first[k + 1]) - 1 if k + 1 < len(first) else total - 1
            first[k] = max(room_low, min(int(round(expected[i] / STEP)), room_high))
    voiced = 1 - np.exp(emission[:, blank])
    onsets = {i: [] for i in order}
    for (i, position, k), frame in zip(owner, first):
        if k == 0:
            onsets[i].append(int(frame))
    times = [[(u['startTime'], u['endTime']) for u in group] for group in units]
    shown = int(round(SHOWN / STEP))
    report = []
    for n, i in enumerate(order):
        own = list(onsets[i])
        stop = onsets[order[n + 1]][0] if n + 1 < len(order) else total
        if len(own) > 1 and stop - own[-1] < shown:
            # the last unit would flash by before the line changes: it begins early enough to be
            # seen, and the units before it give way as far as needed (60 ms each at least)
            own[-1] = max(own[0] + 3 * (len(own) - 1), min(own[-1], stop - shown))
            for position in range(len(own) - 2, 0, -1):
                if own[position] <= own[position + 1] - 3:
                    break  # this unit and the ones before it stay where they were heard
                own[position] = own[position + 1] - 3
        row = []
        for position, onset in enumerate(own):
            if position + 1 < len(own):
                end = own[position + 1]
            else:
                # the last unit lasts while the voice goes on (pauses under 0.3 s bridged), but
                # not into other words: singing or speech that is not in the lyrics may follow
                voice_stop = min(stop, total)
                if spoken_at is not None:
                    held = prons[i][-1][-1:]
                    for n in np.flatnonzero(spoken_at > onset + 15):
                        if spoken_at[n] >= voice_stop:
                            break
                        if spoken[n] != held:  # the same vowel again is the note being held
                            voice_stop = int(spoken_at[n])
                            break
                end, pause = onset + 1, 0
                for frame in range(onset + 1, voice_stop):
                    if voiced[frame] > .5:
                        end, pause = frame + 1, 0
                    else:
                        pause += 1
                        if pause >= 15:
                            break
                end = min(max(end + 3, onset + shown), max(stop, onset + 1))
            row.append((round(onset * STEP, 2), round(max(end, onset + 1) * STEP, 2)))
        if i in pinned:
            row[0] = (pinned[i], max(row[0][1], round(pinned[i] + STEP, 2)))
        for position in range(1, len(row)):  # no unit may be empty
            if row[position][0] < row[position - 1][0] + STEP - 1e-9:
                start = round(row[position - 1][0] + STEP, 2)
                row[position] = (start, max(row[position][1], round(start + STEP, 2)))
            row[position - 1] = (row[position - 1][0], row[position][0])
        times[i] = row
        own_tokens = np.flatnonzero(line_of == i)
        share = float((anchor[own_tokens][leading[own_tokens]] >= 0).mean())  # of its units, how many were heard
        start = row[0][0]
        flags = []
        if i in found_elsewhere and i not in pinned and (i not in expected or abs(start - expected[i]) > .5):
            flags.append(f'平台歌词时间那里没听到这句，在 {clock(start)} 听到了，就放在这里（和平台时间差 {abs(start - expected[i]):.1f} 秒），请听一下' if i in expected else f'这句是按听到的位置放的（{clock(start)}），请听一下')
        elif i not in pinned and i in expected and abs(start - expected[i]) > DOUBT:
            flags.append(f'这句比平台歌词的时间{"晚" if start > expected[i] else "早"} {abs(start - expected[i]):.1f} 秒，请听一下')
        elif i not in pinned and share < .34 and leading[own_tokens].sum() >= 4 and (japanese or float((anchor[leading] >= 0).mean()) >= FOLLOWED):
            flags.append('这句的音模型大多没听出来（可能唱的和歌词不一样，或被伴奏盖住），位置是推出来的，请听一下')
        report.append(dict(line=i + 1, old=units[i][0]['startTime'], new=start, expected=expected.get(i), share=share, opening=bool(anchor[opening[i]] >= 0),
                           pinned=i in pinned, kept=i in kept_place, flags=flags, end=row[-1][1],
                           pins=[None if anchor[k] < 0 else round(float(anchor[k]) * STEP, 2) for k in own_tokens if leading[k]]))
    # English lines of which the two models heard less than half (they hear English as if it were
    # Japanese, which often works and sometimes does not): the words are placed again by the
    # aligner that knows English, inside the stretch the line has been given (from just before
    # its start to where the next line begins). Lines they did hear are left alone: there the
    # English aligner, working on the full mix, was the worse of the two when tried.
    english = []
    if second and japanese:
        wave = None
        for n, i in enumerate(order):
            if not is_english(lines[i].get('fullText', '')) or len(times[i]) < 2 or report[n]['share'] >= .5:
                continue
            if wave is None:
                wave = mix_of(source)
            start = times[i][0][0]
            stop = times[order[n + 1]][0][0] if n + 1 < len(order) else length
            before = times[order[n - 1]][-1][0] + STEP if n else 0.0
            a = max(before, start - (0 if i in pinned else .5))
            b = min(stop, a + 25)
            found = english_times(wave[int(a * 16000):int(b * 16000)], [u.get('text', '') for u in units[i]])
            if not found or found[0] is None:
                continue
            row, last = [], None
            for position, span in enumerate(found):
                begin = round(a + span[0], 2) if span else last
                if position == 0 and i in pinned:
                    begin = pinned[i]
                if last is not None:
                    begin = max(begin, round(last + STEP, 2))
                row.append(begin); last = begin
            if row[-1] >= b - STEP:
                continue  # it ran out of room: the line is left as it was
            end = min(b, max(round(a + found[-1][1], 2) if found[-1] else row[-1], row[-1] + SHOWN))
            times[i] = [(begin, row[position + 1] if position + 1 < len(row) else round(end, 2)) for position, begin in enumerate(row)]
            report[n]['new'], report[n]['end'] = times[i][0][0], times[i][-1][1]
            report[n]['flags'] = [f for f in report[n]['flags'] if '没听出来' not in f] + ['英语句，两个日语模型没听清，句内的词是用英语对齐模型排的，请听一下']
            english.append(i + 1)
    # nothing of a line may be left on screen once the next line has begun
    for n in range(len(order) - 1):
        row, limit = times[order[n]], times[order[n + 1]][0][0]
        for position in range(len(row) - 1, -1, -1):
            start, end = row[position]
            if end <= limit:
                break
            start = min(start, round(limit - STEP * (len(row) - position), 2))
            row[position] = (start, min(end, round(limit - STEP * (len(row) - 1 - position), 2)))
            limit = start + STEP * (len(row) - 1 - position)
        for position in range(len(row) - 1):
            row[position] = (row[position][0], min(row[position][1], row[position + 1][0]))
        report[n]['end'] = row[-1][1]
    apply(lines, units, times, only=set(order))
    return doc, dict(key=key, offset=offset, spread=spread, heard=float((anchor[leading] >= 0).mean()), added=added, cancelled=cancelled, overruled=overruled, second=bool(others), readings=1 + len(others), least=LEAST if japanese else LEAST_OTHER, pace=pace, english=english, lines=report)


SAME, FEW, LARGE = .2, 3, 1.0  # lines moved by the same amount: within SAME seconds, FEW or more; a LARGE move is listed by itself


def review_note(rows):
    """What the listening page says about a re-drafted song (rows: report['lines'] of plan()).
    Lines that moved by the same amount as several others are one thing to hear, not many: the
    first of them stands for all. Listed one by one are only the lines that moved a lot and the
    lines the tool is not sure of; the rest carry a mark on their line and need no listening."""
    moved = [r for r in rows if abs(r['new'] - r['old']) > .15 and not r['pinned']]
    doubts = [r for r in rows if r['flags']]
    pinned = [r for r in rows if r['pinned']]
    shift = lambda r: r['new'] - r['old']
    # the usual amount: the one most of the moved lines share
    alike = []
    if len(moved) >= FEW:
        centre = max(moved, key=lambda c: sum(abs(shift(r) - shift(c)) <= SAME / 2 for r in moved))
        usual = float(np.median([shift(r) for r in moved if abs(shift(r) - shift(centre)) <= SAME / 2]))
        alike = [r for r in moved if abs(shift(r) - usual) <= SAME]
        if len(alike) < FEW:
            alike = []
    large = [r for r in moved if abs(shift(r)) >= LARGE and r not in alike]
    small = [r for r in moved if r not in alike and r not in large]
    word = lambda seconds: '晚' if seconds > 0 else '早'
    parts, focus = [], []
    if alike:
        first = alike[0]
        parts.append(f"有 {len(alike)} 行都比音乐文件里的{word(usual)}约 {abs(usual):.1f} 秒，是同一种改动，听第一处（第 {first['line']} 行）就行")
        focus.append({'start': max(0, min(first['old'], first['new']) - 4), 'end': max(first['old'], first['new']) + 3, 'label': f"第 {first['line']} 行（代表 {len(alike)} 行）"})
    if large:
        parts.append(f'{len(large)} 行挪了 1 秒以上，逐个列在按钮里')
        focus += [{'start': max(0, min(r['old'], r['new']) - 4), 'end': max(r['old'], r['new']) + 3, 'label': f"第 {r['line']} 行（{word(shift(r))} {abs(shift(r)):.1f} 秒）"} for r in large[:10]]
    listed = [r for r in doubts if r not in large][:6]
    if doubts:
        parts.append(f'{len(doubts)} 行模型没把握' + ('，逐个列在按钮里' if len(listed) == len([r for r in doubts if r not in large]) else f'，按钮里列了前 {len(listed)} 行，其余在行上有标记'))
        focus += [{'start': max(0, r['new'] - 4), 'end': r['new'] + 3, 'label': f"第 {r['line']} 行（没把握）"} for r in listed]
    if small:
        parts.append(f'另有 {len(small)} 行只挪了不到 1 秒，行上有标记，不用专门听')
    if focus:
        note = '只听下面按钮列出的几处，每处看换到这一句的时候是不是正好开唱。这一版是用新流程从头打的轴：' + '；'.join(parts) + '。'
    elif small:
        note = f'没有需要专门听的地方，随便听一两处换句就行。这一版是用新流程从头打的轴：{len(small)} 行的句首挪了不到 1 秒（行上有标记），其余没动。'
    else:
        note = '没有需要专门听的地方，随便听一两处换句就行。这一版是用新流程从头打的轴，句首基本没动，改的是每句赶在下一句之前结束、最后一个字来得及亮。'
    kept = [r for r in rows if r.get('kept') and not r['pinned']]
    if kept:
        note += f'有 {len(kept)} 行在你确认过的版本里离平台歌词的时间很远，这次按你确认过的位置找，没有拉回平台时间。'
    if pinned:
        note += f'你手动放过的 {len(pinned)} 行原样保留。'
    note += '不对的行直接用微调改。音乐文件里还是改之前的版本，没问题就点通过。'
    flags = [{'line': r['line'], 'text': (f"和另外 {len(alike) - 1} 行一样，" if r in alike else '') + f"句首比音乐文件里的{word(shift(r))} {abs(shift(r)):.2f} 秒（原来 {clock(r['old'])}）"} for r in moved]
    flags += [{'line': r['line'], 'text': '你手动放的位置，原样保留'} for r in pinned]
    flags += [{'line': r['line'], 'text': text} for r in doubts for text in r['flags']]
    return {'priority': True, 'first': True, 'show': 'note', 'note': note, 'focus': focus, 'flags': flags}


def evaluate_hand():
    """Every line the user has placed by hand, in songs already written with this tool: where
    would the tool put it now, on its own? Nothing is written."""
    rows = []
    only = {a for a in sys.argv[1:] if not a.startswith('--')}
    for folder in sorted(OUT.iterdir()):
        if only and folder.name not in only:
            continue
        result = read(folder / 'result.json') if folder.is_dir() else None
        placed = (read(folder / 'timing-edits.applied.json') or {}).get('l') if result else None
        if not placed or result.get('status') != 'complete' or not any('整首一次对齐' in issue for issue in result.get('issues', [])):
            continue
        lines = read(folder / 'aligned.fia')['lyrics']['lines']
        try:
            _, report = plan(folder.name, pins=False, second='--no-second' not in sys.argv, separate='--separate' in sys.argv)
        except ValueError as error:
            print(folder.name, 'skipped:', error); continue
        found = {r['line']: r for r in report['lines']}
        errors = []
        for index in placed:
            group = leaves(lines[int(index)])
            if group and int(index) + 1 in found:
                errors.append((int(index) + 1, round(found[int(index) + 1]['new'] - group[0]['startTime'], 2)))
        rows += [e for _, e in errors]
        print(folder.name, str((read(folder / 'source.json') or {}).get('name', ''))[:24], f"heard {report['heard']:.0%} |", ' '.join(f'L{n} {e:+.2f}' for n, e in errors), flush=True)
    size = np.abs(np.asarray(rows))
    print(f'{len(size)} lines placed by hand: median {np.median(size):.2f}s, within 0.15s {int((size <= .15).sum())}, within 0.5s {int((size <= .5).sum())}, over 1s {int((size > 1).sum())}')


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if '--eval-hand' in sys.argv:
        return evaluate_hand()
    if not args:
        print(__doc__); return
    for key in args:
        folder = OUT / key
        try:
            doc, report = plan(key, second='--no-second' not in sys.argv, separate='--separate' in sys.argv)
        except ValueError as error:
            print(key, 'failed:', error); continue
        source = read(folder / 'source.json') or {}
        rows = report['lines']
        moved = [r for r in rows if abs(r['new'] - r['old']) > .15 and not r['pinned']]
        doubts = [r for r in rows if r['flags']]
        pinned = [r for r in rows if r['pinned']]
        late = sum(1 for a, b in zip(rows, rows[1:]) if a['end'] > b['new'] + .005)
        print(f"{key} {str(source.get('name', ''))[:34]}: offset {report['offset']:+.2f}s spread {report['spread']:.2f}s, {report['heard']:.0%} of the units heard"
              + (f" ({report['readings']} readings: others added {report['added']}, overruled {report['overruled']}, cancelled {report['cancelled']})" if report['second'] else ' (one reading only)')
              + f" | starts changed by more than 0.15s: {len(moved)}/{len(rows)} | kept as placed by hand: {[r['line'] for r in pinned]}"
              f" | doubts: {[r['line'] for r in doubts]} | lines ending after the next begins: {late}")
        if '--write' not in sys.argv:
            continue
        if report['heard'] < report['least']:
            print(f"   NOT written: only {report['heard']:.0%} of the song was heard where the lines are expected, so these times are not to be trusted")
            continue
        if not (folder / BACKUP).exists() or (read(folder / 'result.json') or {}).get('status') == 'complete':
            shutil.copyfile(folder / 'aligned.fia', folder / BACKUP)
        (folder / 'aligned.fia').write_text(json.dumps(doc, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        # the adjustments that were waiting on the previous draft are part of this one now: they
        # join the record of hand-placed lines (as embed_confirmed.mjs does when it applies them)
        pending = read(folder / 'timing-edits.json')
        if pending:
            kept = read(folder / 'timing-edits.applied.json') or {'g': 0, 'l': {}}
            for index, edit in (pending.get('l') or {}).items():
                kept['l'][index] = {'d': round((kept['l'].get(index, {}).get('d') or 0) + (edit.get('d') or 0), 2), 't': edit.get('t')}
            (folder / 'timing-edits.applied.json').write_text(json.dumps(kept, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            (folder / 'timing-edits.json').unlink()
        result = read(folder / 'result.json')
        if result.get('status') == 'complete':
            result['wasStatus'], result['status'] = 'complete', 'needs_review'
        result['issues'] = [issue for issue in result.get('issues', []) if '整首一次对齐' not in issue] \
            + [f'整首一次对齐（听得出的音直接定位、平台时间划范围）：{len(moved)} 行的句首和音乐文件里的不同，试听确认后写入']
        (folder / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        notes = read(OUT / 'review-notes.json') or {}
        notes[key] = review_note(rows)
        (OUT / 'review-notes.json').write_text(json.dumps(notes, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print('   draft written; the version in the music file is kept as', BACKUP)


if __name__ == '__main__':
    main()
