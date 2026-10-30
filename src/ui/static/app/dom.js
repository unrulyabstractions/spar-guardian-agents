/* DOM helpers and formatting for the results browser.
 * h() builds an element and puts every string in a text node. The fmt functions turn numbers, times and
 * scores into short labels.
 *
 *   import { h, fmtInt } from './dom.js';
 *   h('p', null, 'Runs: ', fmtInt(12));
 */

export const enc = encodeURIComponent;
export const DASH = '—';
const BOOL_PROPS = new Set(['checked', 'selected', 'disabled', 'hidden', 'open']);

/** Build an element. Strings in children become text nodes; attrs never take HTML. */
export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  let value;
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === 'class') el.className = Array.isArray(v) ? v.filter(Boolean).join(' ') : v;
      else if (k === 'text') el.textContent = String(v);
      else if (k === 'value') value = v;
      else if (k === 'dataset') Object.assign(el.dataset, v);
      else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
      else if (BOOL_PROPS.has(k)) el[k] = !!v;
      else el.setAttribute(k, v === true ? '' : String(v));
    }
  }
  appendAll(el, children);
  if (value !== undefined) el.value = value;
  return el;
}

export function appendAll(el, children) {
  for (const c of children) {
    if (c == null || c === false || c === true) continue;
    if (Array.isArray(c)) appendAll(el, c);
    else el.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

export function debounce(fn, ms) {
  let t = null;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

export function fmtInt(n) {
  return n == null || Number.isNaN(Number(n)) ? DASH : Number(n).toLocaleString('en-US');
}

export function fmtCompact(n) {
  if (n == null || Number.isNaN(Number(n))) return DASH;
  n = Number(n);
  const a = Math.abs(n);
  if (a >= 1e6) return (n / 1e6).toFixed(a >= 1e7 ? 1 : 2) + 'M';
  if (a >= 1e4) return Math.round(n / 1e3) + 'k';
  if (a >= 1e3) return (n / 1e3).toFixed(1) + 'k';
  return String(n);
}

export function fmtSecs(s) {
  if (s == null || Number.isNaN(Number(s))) return DASH;
  s = Number(s);
  if (s < 60) return s.toFixed(s < 10 ? 2 : 1) + 's';
  const m = Math.floor(s / 60);
  const r = Math.floor(s % 60);
  if (m < 60) return `${m}m ${String(r).padStart(2, '0')}s`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`;
}

export function fmtUsd(x) {
  if (x == null || Number.isNaN(Number(x))) return DASH;
  x = Number(x);
  return '$' + x.toFixed(x < 1 ? 3 : 2);
}

export function fmtTime(iso) {
  if (!iso) return DASH;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toISOString().replace('T', ' ').slice(0, 19) + 'Z';
}

export function fmtScore(score) {
  if (!score || typeof score !== 'object') return DASH;
  const vals = Object.values(score);
  if (!vals.length) return DASH;
  return vals.map(v => (typeof v === 'number' ? String(+v.toFixed(3)) : String(v))).join(' / ');
}

export function firstLine(text, max = 120) {
  const s = String(text == null ? '' : text);
  const line = (s.split('\n').find(l => l.trim()) || '').trim();
  return line.length > max ? line.slice(0, max - 1) + '…' : line;
}

export function asText(v) {
  if (v == null) return '';
  return typeof v === 'string' ? v : JSON.stringify(v, null, 2);
}

export function plural(n, word, many) {
  return `${fmtInt(n)} ${n === 1 ? word : (many || word + 's')}`;
}
