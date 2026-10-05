const TIME_KEYS = new Set(["startTime", "endTime"]);
// Hiragana and katakana letters only: the middle dot and the long-vowel mark also appear in
// Chinese transliterations of foreign names, and must not make a translation look Japanese.
const JAPANESE_KANA = /[\u3041-\u3096\u30a1-\u30fa]/u;
const DEFAULT_TRANSLATION_LANGUAGE = "zh-Hans";
const DEFAULT_ORIGINAL_LANGUAGE = "ja";

const roundSeconds = value => Math.round(value * 1e6) / 1e6;

function cloneValue(value) {
  if (Array.isArray(value)) return value.map(cloneValue);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, cloneValue(item)]));
  }
  return value;
}

function shiftedValue(value, deltaSeconds) {
  if (Array.isArray(value)) return value.map(item => shiftedValue(item, deltaSeconds));
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => {
      if (TIME_KEYS.has(key) && typeof item === "number" && Number.isFinite(item)) {
        return [key, roundSeconds(Math.max(0, item - deltaSeconds))];
      }
      return [key, shiftedValue(item, deltaSeconds)];
    }));
  }
  return value;
}

/** Shift every nested startTime/endTime without changing the source objects. */
export function shiftLines(lines, leadMs) {
  const amount = Number.isFinite(leadMs) ? leadMs / 1000 : 0;
  return shiftedValue(Array.isArray(lines) ? lines : [], amount);
}

function translationEntries(line) {
  const entries = [];
  if (typeof line?.translation === "string" && line.translation.length > 0) {
    entries.push({
      role: "translation",
      text: line.translation,
      language: line.alternateTexts?.find(item => item?.role === "translation" && item.text === line.translation)?.language,
    });
  }
  for (const item of Array.isArray(line?.alternateTexts) ? line.alternateTexts : []) {
    if (item?.role === "translation" && typeof item.text === "string" && item.text.length > 0) {
      entries.push({ ...item, role: "translation" });
    }
  }
  return uniqueTextEntries(entries);
}

function uniqueTextEntries(entries) {
  const seen = new Set();
  return entries.filter(entry => {
    const key = [entry.role, entry.language ?? "", entry.text].join("\u0000");
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function hasKana(line) {
  return typeof line?.fullText === "string" && JAPANESE_KANA.test(line.fullText);
}

function mergeAlternateTexts(group, chosenTranslation) {
  const entries = [];
  for (const line of group) {
    if (!Array.isArray(line?.alternateTexts)) continue;
    for (const item of line.alternateTexts) {
      if (item && typeof item === "object" && typeof item.role === "string" && typeof item.text === "string") {
        entries.push(cloneValue(item));
      }
    }
  }
  if (chosenTranslation && !entries.some(item => item.role === "translation" && item.text === chosenTranslation.text && item.language === chosenTranslation.language)) {
    entries.push({ ...chosenTranslation });
  }
  return uniqueTextEntries(entries);
}

/**
 * Collapse equal-start lyric lines into one display line. Kana-bearing text wins
 * as the canonical original when present; otherwise input order is retained.
 */
export function pairBilingual(lines, primary = "original") {
  if (!Array.isArray(lines)) return [];

  const groupsByStart = new Map();
  for (const line of lines) {
    const key = line?.startTime;
    if (!groupsByStart.has(key)) groupsByStart.set(key, []);
    groupsByStart.get(key).push(line);
  }

  return [...groupsByStart.values()].sort((leftGroup, rightGroup) => {
    const leftStart = Number.isFinite(leftGroup[0]?.startTime) ? leftGroup[0].startTime : Number.POSITIVE_INFINITY;
    const rightStart = Number.isFinite(rightGroup[0]?.startTime) ? rightGroup[0].startTime : Number.POSITIVE_INFINITY;
    return leftStart - rightStart;
  }).flatMap(group => {
    // Lines that each bring their own translation are separate sung lines that happen to start
    // together (a duet), not an original with its translation: keep them all as they are.
    if (group.length > 1 && group.filter(line => translationEntries(line).length).length > 1) return group.map(cloneValue);
    const original = group.find(hasKana) ?? group[0];
    if (!original || typeof original !== "object") return cloneValue(original);

    const pairedLine = group.find(line => line !== original && typeof line?.fullText === "string" && line.fullText.length > 0 && line.fullText !== original.fullText);
    const originalText = typeof original.fullText === "string" ? original.fullText : "";
    const inheritedTranslations = group.flatMap(translationEntries);
    const pairedTranslation = pairedLine
      ? {
          ...translationEntries(pairedLine).find(entry => entry.text === pairedLine.fullText),
          role: "translation",
          text: pairedLine.fullText,
        }
      : undefined;
    const translationEntry = pairedTranslation ?? inheritedTranslations[0];
    const originalTranslation = translationEntry?.text;
    const requestedPrimary = primary === "translation" ? "translation" : "original";
    const displayText = requestedPrimary === "translation" && originalTranslation
      ? originalTranslation
      : originalText;
    const secondaryText = displayText === originalText ? originalTranslation : originalText;

    const timingSource = group.find(line => line?.fullText === displayText && Array.isArray(line.words));
    const result = cloneValue(original);
    result.fullText = displayText;
    result.words = timingSource ? cloneValue(timingSource.words) : [];
    if (secondaryText && secondaryText !== displayText) result.translation = secondaryText;
    else delete result.translation;

    if (timingSource !== original) delete result.wordSegments;
    if (group.length > 1) {
      result.endTime = Math.max(...group.map(line => Number.isFinite(line?.endTime) ? line.endTime : original.endTime));
    }

    const chosenEntry = secondaryText
      ? {
          role: "translation",
          text: secondaryText,
          ...(translationEntry?.language ? { language: translationEntry.language } : {}),
        }
      : undefined;
    const alternateTexts = mergeAlternateTexts(group, chosenEntry);
    if (alternateTexts.length > 0) result.alternateTexts = alternateTexts;
    else delete result.alternateTexts;

    return result;
  });
}

function metadataText(metadata, key) {
  const candidates = [metadata?.[key], metadata?.song?.[key], metadata?.lyrics?.[key]];
  for (const candidate of candidates) {
    if (typeof candidate === "string" && candidate.length > 0) return candidate;
    if (Array.isArray(candidate) && typeof candidate[0] === "string") return candidate[0];
  }
  return undefined;
}

function exportedAtValue(value) {
  if (value instanceof Date && Number.isFinite(value.getTime())) return value.toISOString();
  if (typeof value === "string" && Number.isFinite(Date.parse(value))) return new Date(value).toISOString();
  return new Date().toISOString();
}

function compactSongMetadata(metadata) {
  const source = metadata?.song && typeof metadata.song === "object" ? metadata.song : metadata ?? {};
  const song = {};
  for (const key of ["key", "title", "artist", "album"]) {
    if (typeof source[key] === "string" && source[key].length > 0) song[key] = source[key];
  }
  if (typeof source.durationMs === "number" && Number.isFinite(source.durationMs) && source.durationMs > 0) {
    song.durationMs = Math.round(source.durationMs);
  }
  return song;
}

/** Return an indented Folia .fia JSON document with lead baked into every lyric time. */
export function toFia(lines, metadata = {}, leadMs = 0) {
  const shifted = shiftLines(lines, leadMs);
  const lyricMetadata = metadata?.lyrics && typeof metadata.lyrics === "object" ? metadata.lyrics : {};
  const lyrics = {
    lines: shifted,
    isWordByWord: typeof metadata?.isWordByWord === "boolean"
      ? metadata.isWordByWord
      : typeof lyricMetadata.isWordByWord === "boolean"
        ? lyricMetadata.isWordByWord
        : shifted.some(line => Array.isArray(line?.words) && line.words.length > 0),
  };
  for (const key of ["title", "artist"]) {
    const value = metadataText(metadata, key);
    if (value) lyrics[key] = value;
  }
  if (lyricMetadata.ttml && typeof lyricMetadata.ttml === "object") {
    lyrics.ttml = cloneValue(lyricMetadata.ttml);
  }

  const document = {
    format: "folia-lyricdata",
    version: 1,
    exportedAt: exportedAtValue(metadata?.exportedAt),
    song: compactSongMetadata(metadata),
    lyrics,
  };
  if (typeof metadata?.source === "string" && metadata.source.length > 0) document.source = metadata.source;
  return JSON.stringify(document, null, 2) + "\n";
}

function escapeLrcMetadata(value) {
  return String(value).replace(/[\r\n]+/gu, " ").replace(/\]/gu, "）");
}

function lrcTimestamp(seconds) {
  const centiseconds = Math.max(0, Math.round(seconds * 100));
  const minutes = Math.floor(centiseconds / 6000);
  const secondsPart = Math.floor(centiseconds / 100) % 60;
  const fraction = centiseconds % 100;
  return "[" + String(minutes).padStart(2, "0") + ":" + String(secondsPart).padStart(2, "0") + "." + String(fraction).padStart(2, "0") + "]";
}

/** Export LRC lines with baked timestamps and a zero offset tag. */
export function toLrc(lines, metadata = {}, leadMs = 0) {
  const adjusted = shiftLines(lines, leadMs);
  const output = [];
  for (const [key, tag] of [["title", "ti"], ["artist", "ar"], ["album", "al"], ["by", "by"]]) {
    const value = metadataText(metadata, key);
    if (value) output.push("[" + tag + ":" + escapeLrcMetadata(value) + "]");
  }
  output.push("[offset:0]");

  for (const line of adjusted) {
    if (!line || typeof line.fullText !== "string" || line.fullText.length === 0) continue;
    const timestamp = lrcTimestamp(Number.isFinite(line.startTime) ? line.startTime : 0);
    output.push(timestamp + line.fullText);
    const translations = uniqueTextEntries(translationEntries(line));
    const seenTranslations = new Set([line.fullText]);
    for (const entry of translations) {
      if (!seenTranslations.has(entry.text)) {
        output.push(timestamp + entry.text);
        seenTranslations.add(entry.text);
      }
    }
  }
  return output.join("\n") + "\n";
}

function xmlText(value) {
  return String(value).replace(/&/gu, "&amp;").replace(/</gu, "&lt;").replace(/>/gu, "&gt;");
}

function xmlAttribute(value) {
  return xmlText(value).replace(/"/gu, "&quot;").replace(/'/gu, "&apos;");
}

function ttmlTime(seconds) {
  const milliseconds = Math.max(0, Math.round((Number.isFinite(seconds) ? seconds : 0) * 1000));
  const whole = Math.floor(milliseconds / 1000);
  const fraction = milliseconds % 1000;
  return String(whole) + "." + String(fraction).padStart(3, "0") + "s";
}

function timedSpan(text, startTime, endTime) {
  return "<span begin=\"" + ttmlTime(startTime) + "\" end=\"" + ttmlTime(endTime) + "\">" + xmlText(text) + "</span>";
}

function syllableMarkup(syllable, fallbackStart, fallbackEnd) {
  const text = typeof syllable?.text === "string" ? syllable.text : "";
  if (!text) return "";
  const start = Number.isFinite(syllable.startTime) ? syllable.startTime : fallbackStart;
  const end = Number.isFinite(syllable.endTime) ? syllable.endTime : fallbackEnd;
  const ruby = Array.isArray(syllable.ruby) ? syllable.ruby.filter(item => typeof item?.text === "string" && item.text.length > 0) : [];

  if (ruby.length === 0) return timedSpan(text, start, end);
  const rubyText = ruby.map(item => (
    "<span tts:ruby=\"text\" begin=\"" + ttmlTime(item.startTime) + "\" end=\"" + ttmlTime(item.endTime) + "\">" + xmlText(item.text) + "</span>"
  )).join("");
  return "<span tts:ruby=\"container\"><span tts:ruby=\"base\">" + xmlText(text) + "</span><span tts:ruby=\"textContainer\">" + rubyText + "</span></span>";
}

function wordMarkup(word, fullText, searchFrom) {
  const rawText = typeof word?.text === "string" ? word.text : "";
  const core = rawText.trim();
  if (!core) return null;
  const index = fullText.indexOf(core, searchFrom);
  if (index < 0) return null;

  const start = Number.isFinite(word.startTime) ? word.startTime : 0;
  const end = Number.isFinite(word.endTime) ? word.endTime : start;
  let content = "";
  const syllables = Array.isArray(word.syllables) ? word.syllables.filter(item => typeof item?.text === "string" && item.text.length > 0) : [];

  if (syllables.length === 0) {
    content = timedSpan(core, start, end);
  } else {
    let cursor = 0;
    const chunks = [];
    let canUseSyllables = true;
    for (const syllable of syllables) {
      const syllableIndex = core.indexOf(syllable.text, cursor);
      if (syllableIndex < 0) {
        canUseSyllables = false;
        break;
      }
      if (syllableIndex > cursor) chunks.push(timedSpan(core.slice(cursor, syllableIndex), start, end));
      chunks.push(syllableMarkup(syllable, start, end));
      cursor = syllableIndex + syllable.text.length;
    }
    if (canUseSyllables) {
      if (cursor < core.length) chunks.push(timedSpan(core.slice(cursor), start, end));
      content = chunks.join("");
    } else {
      content = timedSpan(core, start, end);
    }
  }

  return { index, endIndex: index + core.length, content };
}

function lineTimedContent(line) {
  const fullText = typeof line?.fullText === "string"
    ? line.fullText
    : (Array.isArray(line?.words) ? line.words.map(word => word?.text ?? "").join("") : "");
  const words = Array.isArray(line?.words) ? line.words : [];
  let cursor = 0;
  let content = "";

  for (const word of words) {
    const piece = wordMarkup(word, fullText, cursor);
    if (!piece) return timedSpan(fullText, line?.startTime, line?.endTime);
    content += xmlText(fullText.slice(cursor, piece.index));
    content += piece.content;
    cursor = piece.endIndex;
  }
  content += xmlText(fullText.slice(cursor));
  return content || timedSpan(fullText, line?.startTime, line?.endTime);
}

function lineTextEntries(line, role) {
  const result = [];
  if (role === "translation" && typeof line?.translation === "string" && line.translation.length > 0) {
    const annotated = line.alternateTexts?.find(item => item?.role === role && item.text === line.translation);
    result.push({
      role,
      text: line.translation,
      language: annotated?.language,
    });
  }
  for (const item of Array.isArray(line?.alternateTexts) ? line.alternateTexts : []) {
    if (item?.role === role && typeof item.text === "string" && item.text.length > 0) {
      result.push({ role, text: item.text, language: item.language });
    }
  }
  return uniqueTextEntries(result);
}

function translationMarkup(line, metadata) {
  const translations = lineTextEntries(line, "translation");
  const romanizations = lineTextEntries(line, "romanization");
  const render = entry => {
    const ttmlRole = entry.role === "translation" ? "x-translation" : "x-roman";
    const language = entry.language || (entry.role === "translation"
      ? metadata?.translationLanguage || DEFAULT_TRANSLATION_LANGUAGE
      : metadata?.romanizationLanguage || DEFAULT_ORIGINAL_LANGUAGE);
    return "<span ttm:role=\"" + ttmlRole + "\" xml:lang=\"" + xmlAttribute(language) + "\">" + xmlText(entry.text) + "</span>";
  };
  return [...translations, ...romanizations].map(render).join("");
}

/** Export TTML2 with timed spans, inline translations, and nested tts:ruby groups. */
export function toTtml(lines, metadata = {}, leadMs = 0) {
  const adjusted = shiftLines(lines, leadMs);
  const title = metadataText(metadata, "title");
  const artist = metadataText(metadata, "artist");
  const album = metadataText(metadata, "album");
  const language = metadata?.language || DEFAULT_ORIGINAL_LANGUAGE;
  const metadataNodes = [];
  if (title) metadataNodes.push("<amll:meta key=\"musicName\" value=\"" + xmlAttribute(title) + "\"/>");
  if (artist) metadataNodes.push("<amll:meta key=\"artists\" value=\"" + xmlAttribute(artist) + "\"/>");
  if (album) metadataNodes.push("<amll:meta key=\"album\" value=\"" + xmlAttribute(album) + "\"/>");

  const paragraphs = adjusted.map((line, index) => {
    const start = Number.isFinite(line?.startTime) ? line.startTime : 0;
    const end = Number.isFinite(line?.endTime) ? line.endTime : start;
    return "<p begin=\"" + ttmlTime(start) + "\" end=\"" + ttmlTime(end) + "\" itunes:key=\"L" + (index + 1) + "\">"
      + lineTimedContent(line)
      + translationMarkup(line, metadata)
      + "</p>";
  }).join("");

  return "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
    + "<tt xmlns=\"http://www.w3.org/ns/ttml\" xmlns:tts=\"http://www.w3.org/ns/ttml#styling\""
    + " xmlns:ttm=\"http://www.w3.org/ns/ttml#metadata\" xmlns:amll=\"http://www.example.com/ns/amll\""
    + " xmlns:itunes=\"http://music.apple.com/lyric-ttml-internal\" xml:lang=\"" + xmlAttribute(language)
    + "\" itunes:timing=\"Word\"><head><metadata>" + metadataNodes.join("")
    + "</metadata></head><body><div>" + paragraphs + "</div></body></tt>";
}




