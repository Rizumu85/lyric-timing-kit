// Reading aid for Korean lyrics: put the Revised Romanization over every Hangul syllable, the
// way furigana sits over kanji. Works on a draft FIA document in place; words that already
// have ruby are left alone. Used for the listening page and when lyrics are written to an MP3.
const INITIAL = ['g', 'kk', 'n', 'd', 'tt', 'r', 'm', 'b', 'pp', 's', 'ss', '', 'j', 'jj', 'ch', 'k', 't', 'p', 'h'];
const MEDIAL = ['a', 'ae', 'ya', 'yae', 'eo', 'e', 'yeo', 'ye', 'o', 'wa', 'wae', 'oe', 'yo', 'u', 'wo', 'we', 'wi', 'yu', 'eu', 'ui', 'i'];
// final consonant: [as a syllable end, when it moves onto a following vowel]
const FINAL = [['', ''], ['k', 'g'], ['k', 'kk'], ['k', 'ks'], ['n', 'n'], ['n', 'nj'], ['n', 'n'], ['t', 'd'], ['l', 'r'], ['k', 'lg'], ['m', 'lm'], ['l', 'lb'], ['l', 'ls'], ['l', 'lt'], ['p', 'lp'], ['l', 'r'],
  ['m', 'm'], ['p', 'b'], ['p', 'bs'], ['t', 's'], ['t', 'ss'], ['ng', 'ng'], ['t', 'j'], ['t', 'ch'], ['k', 'k'], ['t', 't'], ['p', 'p'], ['t', 'h']];
const isHangul = char => char >= '가' && char <= '힣';
const parts = char => { const code = char.charCodeAt(0) - 0xac00; return [Math.floor(code / 588), Math.floor(code % 588 / 28), code % 28]; };

// one romanized piece per character of the text ('' for anything that is not Hangul)
export function romanize(text) {
  const chars = [...text];
  return chars.map((char, index) => {
    if (!isHangul(char)) return '';
    const [initial, medial, final] = parts(char);
    const previous = index && isHangul(chars[index - 1]) ? parts(chars[index - 1]) : null;
    const next = isHangul(chars[index + 1] || '') ? parts(chars[index + 1]) : null;
    // a final consonant before a vowel is pronounced at the start of the next syllable (걸어 = geo-reo)
    const carried = previous && initial === 11 && previous[2] && previous[2] !== 21 ? FINAL[previous[2]][1] : '';
    const ending = final && next && next[0] === 11 && final !== 21 ? '' : FINAL[final][0];
    return (carried || INITIAL[initial]) + MEDIAL[medial] + ending;
  });
}

export function addHangulRuby(doc) {
  let added = 0;
  for (const line of doc.lyrics.lines) for (const word of line.words || []) {
    if (word.ruby?.length || word.syllables?.length || ![...word.text].some(isHangul)) continue;
    const chars = [...word.text], readings = romanize(word.text);
    // the word's time is shared out over its Hangul syllables; other characters take none
    const count = readings.filter(Boolean).length, step = (word.endTime - word.startTime) / count;
    let at = word.startTime;
    word.syllables = chars.map((char, index) => {
      const start = at, end = readings[index] ? Math.round((at + step) * 100) / 100 : at;
      at = end;
      return { text: char, startTime: Math.round(start * 100) / 100, endTime: end, ...(readings[index] ? { ruby: [{ text: readings[index], startTime: Math.round(start * 100) / 100, endTime: end }] } : {}) };
    });
    added += count;
  }
  return added;
}
