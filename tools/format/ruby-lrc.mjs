import { pairBilingual, shiftLines } from './lyrics.mjs';

const tag = seconds => {
  const ticks = Math.max(0, Math.round(seconds * 100));
  return `[${String(Math.floor(ticks / 6000)).padStart(2, '0')}:${String(Math.floor(ticks / 100) % 60).padStart(2, '0')}:${String(ticks % 100).padStart(2, '0')}]`;
};
const safe = text => {
  if (/[\r\n\[\]]/.test(text)) throw new Error('歌词含时间标签保留字符，无法无损导出 Ruby LRC；请使用 TTML');
  return text;
};

/** SHINTA TimeTag Ruby extension. Absolute base tags, relative reading tags. */
export function toRubyLrc(lines, metadata = {}, leadMs = 0) {
  const output = [], footer = [];
  for (const [key, id] of [['title', 'ti'], ['artist', 'ar']]) {
    if (metadata[key]) output.push(`[${id}:${String(metadata[key]).replace(/[\r\n\]]/g, ' ')}]`);
  }
  for (const line of shiftLines(pairBilingual(lines), leadMs)) {
    if (!line.fullText) continue;
    let parts = [];
    for (const word of line.words || []) {
      const syllables = word.syllables;
      const syllableText = syllables?.map(s => s.text).join('');
      const matching = syllableText && word.text.startsWith(syllableText);
      if (!matching && syllables?.some(s => s.ruby?.length)) throw new Error('假名对应文本与歌词不一致；请使用 TTML');
      const segments = matching ? [...syllables] : [word];
      if (matching && syllableText !== word.text) segments.push({ text: word.text.slice(syllableText.length), startTime: word.endTime, endTime: word.endTime });
      parts.push(...segments);
      if (word.endsWithSpace) parts.push({ text: ' ', startTime: word.endTime, endTime: word.endTime });
    }
    if (parts.map(part => part.text).join('') !== line.fullText) {
      if (parts.some(part => part.ruby?.length)) throw new Error('逐字文本与整句不一致，无法保留全部假名；请使用 TTML');
      parts = [{ text: line.fullText, startTime: line.startTime, endTime: line.endTime }];
    }
    let row = tag(line.startTime);
    for (const part of parts) {
      row += tag(part.startTime) + safe(part.text) + tag(part.endTime);
      if (part.ruby?.length) {
        if (part.text.includes(',') || part.ruby.some(r => /[,\r\n\[\]]/.test(r.text))) throw new Error('假名组含 Ruby 规范保留字符，无法导出');
        const origin = Math.round(part.startTime * 100) / 100;
        const reading = part.ruby.map(r => tag(Math.max(0, Math.round(r.startTime * 100) / 100 - origin)) + r.text + tag(Math.max(0, Math.round(r.endTime * 100) / 100 - origin))).join('');
        // Both inclusive application boundaries point to this exact occurrence;
        // the reading's explicit final tag retains its end independently.
        footer.push(`@Ruby${footer.length+1}=${part.text},${reading},${tag(part.startTime)},${tag(part.startTime)}`);
      }
    }
    output.push(row);
    const translations = new Set([line.translation, ...(line.alternateTexts || []).filter(t => t.role === 'translation').map(t => t.text)]);
    for (const text of translations) if (text && text !== line.fullText) output.push(tag(line.startTime) + safe(text) + tag(line.endTime));
  }
  return ([...output, '@Offset=0', ...footer].join('\n') + '\n').replace(/(\[\d+:\d{2}:\d{2}\])\1/g, '$1');
}
