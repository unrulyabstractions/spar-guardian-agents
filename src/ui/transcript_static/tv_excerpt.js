/* A judge's excerpt in the text it was located in: the span code found (src/judging/judge_records.py locate) checked
 * against the text the page shows, and the text drawn with the excerpt marked. The page matches as the code does
 * (plain): whitespace collapsed, markdown backticks dropped, curly quotes and apostrophes made straight. Every string
 * goes in through h() text nodes. */
import { h } from '/static/app/dom.js';

const STRAIGHT = { '‘': '\'', '’': '\'', '‚': '\'', '‛': '\'', '′': '\'',
  '“': '"', '”': '"', '„': '"', '‟': '"', '″': '"' };

/** text in its plain form and, for each of its characters, the index of the character of text it came from. */
function plainMap(text) {
  let out = '';
  const at = [];
  let space = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (c === '`') continue;
    if (/\s/.test(c)) { space = out.length > 0; continue; }
    if (space) { out += ' '; at.push(i - 1); space = false; }
    out += STRAIGHT[c] || c;
    at.push(i);
  }
  return { out, at };
}
const plain = text => plainMap(String(text)).out;

/** The [start, end) of the excerpt in text: code's span where the text the page shows holds the excerpt there, else
 * the first place its plain form holds it; null when it is not in the text. */
export function excerptSpan(text, excerpt, start, end) {
  const want = plain(excerpt);
  if (!want || text == null) return null;
  if (start != null && end != null && plain(text.slice(start, end)) === want) return [start, end];
  const { out, at } = plainMap(text);
  const i = out.indexOf(want);
  return i < 0 ? null : [at[i], at[i + want.length - 1] + 1];
}

/** The text as nodes with the span marked (class xl-mark): before, the mark, after. */
export const markedNodes = (text, span) => [text.slice(0, span[0]),
  h('mark', { class: 'xl-mark' }, text.slice(span[0], span[1])), text.slice(span[1])];
