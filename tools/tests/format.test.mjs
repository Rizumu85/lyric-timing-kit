// The embedded lyric formats must read back exactly what was written. Synthetic lines only.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { toRubyLrc } from '../format/ruby-lrc.mjs';
import { parseFaKara } from '../format/fa-kara.mjs';
import { pairBilingual } from '../format/lyrics.mjs';
import { addHangulRuby, romanize } from '../hangul_ruby.mjs';
import { applyTimingEdits } from '../timing_edits.mjs';

const ruby = (text, reading, start, end) => ({ text, startTime: start, endTime: end, syllables: [{ text, startTime: start, endTime: end, ruby: [{ text: reading, startTime: start, endTime: end }] }] });
const shape = lines => lines.map(line => [line.fullText, line.translation || '', (line.words || []).flatMap(word => word.syllables || []).filter(part => part.ruby?.length).map(part => `${part.text}=${part.ruby.map(item => item.text).join('')}`)]);
const roundTrip = lines => pairBilingual(parseFaKara(toRubyLrc(lines, {}, 0)).lines);

test('ruby survives the round trip, and stays off the translation line', () => {
  const lines = [{ fullText: '知らない', translation: '不知道', startTime: 10, endTime: 12, words: [ruby('知', 'し', 10, 10.5), { text: 'らない', startTime: 10.5, endTime: 12 }] }];
  assert.deepEqual(shape(roundTrip(lines)), [['知らない', '不知道', ['知=し']]]);
});

test('a reading may span text that is not kanji, and may be katakana', () => {
  const lines = [{ fullText: '夜の手を', startTime: 1, endTime: 3, words: [ruby('夜の手', 'ナイトハンド', 1, 2), { text: 'を', startTime: 2, endTime: 3 }] }];
  assert.deepEqual(shape(roundTrip(lines)), [['夜の手を', '', ['夜の手=ナイトハンド']]]);
});

test('the same character can carry different readings at different times', () => {
  const doc = { lyrics: { lines: [{ fullText: '걸어 어', startTime: 0, endTime: 3, words: [{ text: '걸어 ', startTime: 0, endTime: 2 }, { text: '어', startTime: 2, endTime: 3 }] }] } };
  assert.equal(addHangulRuby(doc), 3);
  assert.deepEqual(shape(roundTrip(doc.lyrics.lines))[0][2], ['걸=geo', '어=reo', '어=eo']);
});

test('Hangul romanization carries a final consonant onto a following vowel', () => {
  assert.equal(romanize('걸어들어와').join('-'), 'geo-reo-deu-reo-wa');
  assert.deepEqual(romanize('백 퍼').filter(Boolean), ['baek', 'peo']);
});

test('timing edits move a whole line and refuse a line whose text changed', () => {
  const doc = { lyrics: { lines: [{ fullText: 'a', startTime: 1, endTime: 2, words: [{ text: 'a', startTime: 1, endTime: 2 }] }] } };
  assert.equal(applyTimingEdits(doc, { g: 0, l: { 0: { d: 0.5, t: 'a' } } }), 1);
  assert.equal(doc.lyrics.lines[0].words[0].startTime, 1.5);
  assert.throws(() => applyTimingEdits(doc, { g: 0, l: { 0: { d: 0.5, t: 'b' } } }));
});
