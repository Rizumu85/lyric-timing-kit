"""Time the words inside given lines again, where the lines now are.

A line moved by hand on the listening page moves as a block: its words keep the spacing they had
where the line was before. After a large move that spacing was fitted to other audio, so the
words no longer light when they are sung. This tool keeps the start of each given line exactly
and aligns the line's units again inside [its start, the next line's start), from the saved
emission (ja-emission.pt for Japanese songs, mms-emission.pt for the others). No model is run,
and no other line is touched.

Usage:
  python tools/refit_lines.py <key> <line,line,...>            preview
  python tools/refit_lines.py <key> <line,line,...> --write    write the draft
  ... --even    do not align: spread the units over the stretch in which the voice is heard after
                the line's start (up to the first pause of PAUSE seconds), by their length. For
                lines the models cannot follow (humming, vocalise, English in a Japanese song).
"""
import json
import sys

import numpy as np
import torch

from kit_config import OUT
from onepass import STEP, apply, leaves, read, sung, tokenizer

LAST_SHOWN = .2  # seconds a line's last unit must have before the next line starts
SPARE = 2.0  # a line may grow by this much when the next line is far away
PAUSE = .6  # --even: the voice silent for this long ends the line


def align(emission, tokens, low, high, blank):
    """CTC forced alignment of `tokens` to the whole emission, token k allowed only in frames
    low[k] <= t < high[k]. Returns the first frame of every token."""
    frames, count = emission.shape[0], len(tokens)
    states = 2 * count + 1
    labels = np.full(states, blank, dtype=np.int64)
    labels[1::2] = tokens
    lo = np.zeros(states, dtype=np.int64)
    hi = np.full(states, frames, dtype=np.int64)
    lo[1::2], hi[1::2] = low, high
    # a blank may be skipped between two different tokens
    skip = np.zeros(states, dtype=bool)
    skip[3::2] = np.asarray(tokens[1:]) != np.asarray(tokens[:-1])
    score = np.full(states, -np.inf)
    back = np.zeros((frames, states), dtype=np.int8)
    index = np.arange(states)
    emit = emission[:, labels]  # frames x states
    score[0] = emit[0, 0]
    if lo[1] <= 0:
        score[1] = emit[0, 1]
    for t in range(1, frames):
        stay = score
        step = np.concatenate(([-np.inf], score[:-1]))
        jump = np.where(skip, np.concatenate(([-np.inf, -np.inf], score[:-2])), -np.inf)
        best = np.stack((stay, step, jump))
        choice = best.argmax(0)
        score = best[choice, index] + emit[t]
        score[(t < lo) | (t >= hi)] = -np.inf
        back[t] = choice
    state = states - 1 if score[states - 1] >= score[states - 2] else states - 2
    if not np.isfinite(score[state]):
        raise ValueError('no alignment fits the windows')
    first = np.full(count, -1, dtype=np.int64)
    for t in range(frames - 1, -1, -1):
        if state % 2:
            first[state // 2] = t
        state -= back[t, state]
    if (first < 0).any():
        raise ValueError('a token got no frame')
    return first


def refit(key, wanted, even=False):
    """Returns (doc, lines fitted, {line: why not}); doc is the draft with those lines re-timed."""
    folder = OUT / key
    doc, prepared, source = read(folder / 'aligned.fia'), read(folder / 'prepared.json'), read(folder / 'source.json')
    if not doc or not prepared or not source:
        raise ValueError('draft, pronunciations or lyric sheet missing')
    lines = doc['lyrics']['lines']
    prons = sung(prepared)
    units = [leaves(line) for line in lines]
    if len(prons) != len(lines):
        raise ValueError('draft and saved pronunciations are out of step')
    if source.get('language') == 'ja' and (folder / 'ja-emission.pt').exists():
        emission = torch.load(folder / 'ja-emission.pt', map_location='cpu', weights_only=True).float().numpy()
        tok = tokenizer()
        blank = tok.pad_token_id
        encode = lambda pron: tok.encode(pron, add_special_tokens=False)
    elif (folder / 'mms-emission.pt').exists():
        import torchaudio
        emission = torch.load(folder / 'mms-emission.pt', map_location='cpu', weights_only=True).float().numpy()
        letters = torchaudio.pipelines.MMS_FA.get_dict()
        blank = 0
        encode = lambda pron: [letters[c] for c in pron if c in letters]
    else:
        raise ValueError('no saved emission')
    voiced = 1 - np.exp(emission[:, blank])
    shown = int(round(LAST_SHOWN / STEP))
    done, left = [], {}
    for row in sorted(n - 1 for n in wanted):
        group = units[row]
        if not group or len(group) != len(prons[row]):
            left[row + 1] = 'its units and the saved pronunciations are out of step'
            continue
        start = group[0]['startTime']
        after = next((units[j][0]['startTime'] for j in range(row + 1, len(lines)) if units[j]), None)
        if after is not None and after <= start:
            left[row + 1] = 'the next line does not start after it'
            continue
        span = group[-1]['endTime'] - start
        limit = min(len(emission) * STEP, start + max(span, .3 * len(group)) + SPARE)
        tight = after is not None and after <= limit
        if tight:
            limit = after
        begin, stop = int(round(start / STEP)), int(round(limit / STEP))
        ids, owner = [], []
        for position, pron in enumerate(prons[row]):
            for n, token in enumerate(encode(pron)):
                ids.append(token); owner.append((position, n))
        if {position for position, n in owner} != set(range(len(group))):
            left[row + 1] = 'a unit has no sound the model knows'
            continue
        if stop - begin < len(ids) + shown + 1:
            left[row + 1] = 'no room before the next line'
            continue
        low = np.zeros(len(ids), dtype=np.int64)
        high = np.full(len(ids), stop - begin, dtype=np.int64)
        high[0] = min(stop - begin, 3)  # the line starts where it starts
        last = max(k for k, (position, n) in enumerate(owner) if n == 0)
        if last and tight:
            high[:last + 1] = np.minimum(high[:last + 1], stop - begin - shown + 1)  # the last unit gets its time on screen
        if even:
            on = voiced[begin:stop] > .5
            runs, t = [], 0
            while t < len(on):
                if on[t]:
                    u = t
                    while u < len(on) and on[u]:
                        u += 1
                    runs.append((t, u)); t = u
                else:
                    t += 1
            print(f"   line {row + 1}: voice heard at", ' '.join(f'+{a * STEP:.2f}..+{b * STEP:.2f}' for a, b in runs[:14]))
            if not runs:
                left[row + 1] = 'no voice heard after its start'
                continue
            close = runs[0][1]
            for a, b in runs[1:]:
                if a - close >= PAUSE / STEP:
                    break
                close = b
            close = max(close, len(group) * 3)
            weight = np.cumsum([0] + [max(1, sum(1 for position, n in owner if position == k)) for k in range(len(group))])
            starts = [begin + int(round(close * weight[k] / weight[-1])) for k in range(len(group))]
            stop = min(stop, begin + close + 3)
        else:
            try:
                first = align(emission[begin:stop], np.asarray(ids), low, high, blank) + begin
            except ValueError as error:
                left[row + 1] = str(error)
                continue
            starts = [frame for (position, n), frame in zip(owner, first) if n == 0]
        times = []
        for position, frame in enumerate(starts):
            if position + 1 < len(starts):
                end = starts[position + 1]
            else:
                # the last unit ends where the voice does: the first pause of 0.3 s, or the limit
                end = frame + 1
                quiet = 0
                for t in range(frame + 1, stop):
                    if voiced[t] > .5:
                        end, quiet = t + 1, 0
                    else:
                        quiet += 1
                        if quiet >= 15:
                            break
                end = min(stop, max(end + 3, frame + shown))
            times.append((round(frame * STEP, 2), round(max(end, frame + 1) * STEP, 2)))
        times[0] = (start, max(times[0][1], round(start + STEP, 2)))
        before = [(unit['startTime'], unit['endTime']) for unit in group]
        whole = [[(unit['startTime'], unit['endTime']) for unit in g] for g in units]
        whole[row] = times
        apply(lines, units, whole, only={row})
        done.append(row + 1)
        print(f"   line {row + 1} [{start:.2f}, {'next line ' if tight else 'limit '}{limit:.2f}):")
        print('      was', ' '.join(f"{(unit.get('text') or '').strip()}+{a - start:.2f}" for unit, (a, b) in zip(group, before)), f'| ends +{before[-1][1] - start:.2f}')
        print('      now', ' '.join(f"{(unit.get('text') or '').strip()}+{a - start:.2f}" for unit, (a, b) in zip(group, times)), f'| ends +{times[-1][1] - start:.2f}')
    return doc, done, left


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if len(args) != 2:
        print(__doc__); return
    key, wanted = args[0], {int(n) for n in args[1].split(',')}
    doc, done, left = refit(key, wanted, '--even' in sys.argv)
    print(key, 'fitted', done, '| left as they were', left)
    if '--write' in sys.argv and done:
        (OUT / key / 'aligned.fia').write_text(json.dumps(doc, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print('   draft written')


if __name__ == '__main__':
    main()
