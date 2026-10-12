// Apply the manual timing adjustments saved by the listening page to a draft FIA.
// Format: { g: secondsForWholeSong, l: { "<lineIndex>": { d: seconds, t: lineText } } }
const round = value => Math.round(value * 100) / 100;

function shift(node, delta) {
  for (const key of ['startTime', 'endTime']) if (typeof node[key] === 'number') node[key] = Math.max(0, round(node[key] + delta));
  for (const key of ['words', 'syllables', 'ruby']) for (const child of node[key] || []) shift(child, delta);
}

export function applyTimingEdits(doc, edits) {
  const lines = doc.lyrics.lines;
  let changed = 0;
  lines.forEach((line, index) => {
    const edit = edits.l?.[index];
    if (edit && edit.t !== line.fullText) throw new Error(`第 ${index + 1} 行的歌词在微调之后变了，微调无法套用`);
    const delta = (edits.g || 0) + (edit?.d || 0);
    if (delta) { shift(line, delta); changed++; }
  });
  return changed;
}

// The record of every line the user has placed by hand, kept next to the draft once adjustments
// are applied: an earlier record is added to, not replaced.
export function mergeApplied(previous, edits) {
  const merged = { g: round((previous?.g || 0) + (edits.g || 0)), l: { ...(previous?.l || {}) } };
  for (const [index, edit] of Object.entries(edits.l || {})) merged.l[index] = { d: round((merged.l[index]?.d || 0) + (edit.d || 0)), t: edit.t };
  return merged;
}
