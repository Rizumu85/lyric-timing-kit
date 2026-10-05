// A narrow ID3 frame reader/editor. Non-lyric frames and the entire audio tail
// remain byte-for-byte intact; unsupported write layouts are rejected.
export const RUBY_DESCRIPTOR = 'TimeTag-Ruby';
export const MAX_TAG_BYTES = 16 * 1024 * 1024;
const ascii = bytes => String.fromCharCode(...bytes);
const uint = bytes => bytes.reduce((n, byte) => n * 256 + byte, 0);
const sync = bytes => {
  if (bytes.some(byte => byte & 128)) throw new Error('ID3 长度字段无效');
  return bytes.reduce((n, byte) => n * 128 + byte, 0);
};
const sizeBytes = (size, synchsafe) => {
  const base = synchsafe ? 128 : 256, result = new Uint8Array(4);
  for (let i = 3; i >= 0; i--) { result[i] = size % base; size = Math.floor(size / base); }
  return result;
};
const concat = parts => {
  const result = new Uint8Array(parts.reduce((size, part) => size + part.length, 0));
  let at = 0; for (const part of parts) { result.set(part, at); at += part.length; }
  return result;
};
const deunsync = bytes => {
  const result = [];
  for (let i = 0; i < bytes.length; i++) { result.push(bytes[i]); if (bytes[i] === 255 && bytes[i+1] === 0) i++; }
  return Uint8Array.from(result);
};

export function id3Header(bytes) {
  if (ascii(bytes.subarray(0, 3)) !== 'ID3') return { version: 3, flags: 0, bodySize: 0, totalSize: 0 };
  if (bytes.length < 10 || ![2, 3, 4].includes(bytes[3]) || bytes[4] === 255) throw new Error('不支持或损坏的 ID3 标签');
  const bodySize = sync(bytes.subarray(6, 10));
  const totalSize = 10 + bodySize + (bytes[3] === 4 && bytes[5] & 16 ? 10 : 0);
  if (totalSize > MAX_TAG_BYTES) throw new Error('MP3 的 ID3 标签超过 16 MB');
  return { version: bytes[3], revision: bytes[4], flags: bytes[5], bodySize, totalSize };
}

function decodeText(bytes, encoding, inheritedEndian = 'utf-16le') {
  if (encoding === 0) return Array.from(bytes, byte => String.fromCharCode(byte)).join('').replace(/\0+$/, '');
  let charset = encoding === 3 ? 'utf-8' : encoding === 2 ? 'utf-16be' : inheritedEndian;
  if (encoding === 1 && bytes[0] === 255 && bytes[1] === 254) charset = 'utf-16le';
  if (encoding === 1 && bytes[0] === 254 && bytes[1] === 255) charset = 'utf-16be';
  if (![1, 2, 3].includes(encoding)) throw new Error('不支持的 ID3 歌词编码');
  return new TextDecoder(charset, { fatal: true }).decode(bytes).replace(/^\uFEFF/, '').replace(/\0+$/, '');
}

function readUslt(payload) {
  if (payload.length < 5) throw new Error('ID3 歌词项不完整');
  const encoding = payload[0], wide = encoding === 1 || encoding === 2;
  let terminator = -1;
  for (let at = 4; at < payload.length; at += wide ? 2 : 1) {
    if (payload[at] === 0 && (!wide || payload[at+1] === 0)) { terminator = at; break; }
  }
  if (terminator < 0) throw new Error('ID3 歌词描述缺少结束符');
  const endian = payload[4] === 254 && payload[5] === 255 ? 'utf-16be' : 'utf-16le';
  const descriptor = decodeText(payload.subarray(4, terminator), encoding, endian);
  const text = decodeText(payload.subarray(terminator + (wide ? 2 : 1)), encoding, endian);
  if (text.length > 1024 * 1024) throw new Error('内嵌歌词超过解析上限');
  return { language: ascii(payload.subarray(1, 4)), descriptor, text };
}

export function parseId3(bytes) {
  const header = id3Header(bytes), frames = [], lyrics = [];
  if (!header.totalSize) return { ...header, frames, lyrics };
  if (bytes.length < header.totalSize) throw new Error('ID3 标签不完整');
  let body = bytes.subarray(10, 10 + header.bodySize);
  if (header.version < 4 && header.flags & 128) body = deunsync(body);
  if (header.version === 2 && header.flags & 64) throw new Error('暂不读取压缩的 ID3v2.2 标签');
  let at = 0;
  if (header.version >= 3 && header.flags & 64) {
    at = header.version === 3 ? 4 + uint(body.subarray(0, 4)) : sync(body.subarray(0, 4));
    if (at < 6 || at > body.length) throw new Error('ID3 扩展头无效');
  }
  const width = header.version === 2 ? 6 : 10;
  while (at < body.length && body[at] !== 0) {
    if (at + width > body.length) throw new Error('ID3 帧头不完整');
    const idWidth = header.version === 2 ? 3 : 4;
    const id = ascii(body.subarray(at, at + idWidth));
    if (!new RegExp('^[A-Z0-9]{' + idWidth + '}$').test(id)) throw new Error('ID3 帧标识无效');
    const size = header.version === 2 ? uint(body.subarray(at+3, at+6))
      : (header.version === 4 ? sync : uint)(body.subarray(at+4, at+8));
    const end = at + width + size;
    if (!size || end > body.length) throw new Error('ID3 帧长度无效');
    const flags = header.version === 2 ? 0 : body[at+9];
    const frame = { id, flags, raw: body.slice(at, end) };
    frames.push(frame);
    if (id === 'USLT' || id === 'ULT') {
      let payload = body.subarray(at+width, end);
      const unsupported = header.version === 3 ? flags & 192 : flags & 12;
      if (unsupported) throw new Error('暂不读取压缩或加密的内嵌歌词');
      if (header.version === 4 && (header.flags & 128 || flags & 2)) payload = deunsync(payload);
      if (flags & (header.version === 3 ? 32 : 64)) payload = payload.subarray(1);
      if (header.version === 4 && flags & 1) payload = payload.subarray(4);
      const lyric = readUslt(payload);
      frame.lyric = lyric; lyrics.push(lyric);
    }
    at = end;
  }
  if (body.subarray(at).some(byte => byte !== 0)) throw new Error('ID3 填充区无效');
  return { ...header, frames, lyrics };
}

export function embeddedVersions(lyrics, name = 'MP3') {
  const hasRuby = text => /^@Ruby\d+=/m.test(text);
  const usable = lyrics.filter(item => /\[\d+:\d{2}(?:[:.]\d{2,3})?\]/.test(item.text));
  return usable.map(item => ({ ...item, format: 'lrc', embedded: true,
    kind: hasRuby(item.text) ? 'ruby' : 'plain',
    name: name + ' · ' + (hasRuby(item.text) ? '内嵌 Ruby LRC' : '内嵌普通 LRC'),
  })).sort((a, b) => (a.kind === 'ruby' ? 0 : a.descriptor === '' ? 1 : 2) - (b.kind === 'ruby' ? 0 : b.descriptor === '' ? 1 : 2));
}

function usltFrame(version, descriptor, text) {
  const encode = value => {
    if (version === 4) return new TextEncoder().encode(value);
    const result = new Uint8Array(2 + value.length * 2); result.set([255, 254]);
    for (let i = 0; i < value.length; i++) { result[2+i*2] = value.charCodeAt(i) & 255; result[3+i*2] = value.charCodeAt(i) >> 8; }
    return result;
  };
  const payload = concat([Uint8Array.from([version === 4 ? 3 : 1, 106, 112, 110]), encode(descriptor), new Uint8Array(version === 4 ? 1 : 2), encode(text)]);
  return concat([new TextEncoder().encode('USLT'), sizeBytes(payload.length, version === 4), new Uint8Array(2), payload]);
}

/** Replace default lyric slots and our Ruby slot. Keep other described lyrics. */
export function embedDualLyrics(bytes, { plain, ruby }) {
  for (const text of [plain, ruby]) if (typeof text !== 'string' || !text || text.length > 1024 * 1024) throw new Error('内嵌歌词内容无效');
  if (!/^@Ruby\d+=/m.test(ruby)) throw new Error('当前歌词没有可嵌入的 Ruby 注音');
  const parsed = parseId3(bytes);
  if (![3, 4].includes(parsed.version) || parsed.flags !== 0) throw new Error('写入仅支持无扩展头、无全局去同步的 ID3v2.3 / v2.4；原文件未修改');
  const retained = parsed.frames.filter(frame => !frame.lyric || (frame.lyric.descriptor !== '' && frame.lyric.descriptor !== RUBY_DESCRIPTOR));
  const bodyFrames = [usltFrame(parsed.version, '', plain), usltFrame(parsed.version, RUBY_DESCRIPTOR, ruby), ...retained.map(frame => frame.raw)];
  const frameSize = bodyFrames.reduce((sum, frame) => sum + frame.length, 0);
  const padding = Math.max(1024, parsed.bodySize - frameSize);
  if (frameSize + padding + 10 > MAX_TAG_BYTES) throw new Error('写入后的标签超过 16 MB');
  const header = Uint8Array.from([73, 68, 51, parsed.version, parsed.revision || 0, 0, ...sizeBytes(frameSize + padding, true)]);
  const result = concat([header, ...bodyFrames, new Uint8Array(padding), bytes.subarray(parsed.totalSize)]);
  const check = parseId3(result);
  if (check.lyrics[0].text !== plain || check.lyrics[1].text !== ruby) throw new Error('内嵌歌词回读验证失败');
  return result;
}
