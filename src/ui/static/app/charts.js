/* Chart helpers of the results browser.
 * They hold the SVG builder, the role glyphs, the shared tooltip, the colour ramp, the axis ticks, the legend
 * glyphs and a small job queue.
 *
 *   import { svgEl, withTip } from './charts.js';
 *   const box = svgEl('svg', { width: 200, height: 40 });
 */
import { appendAll, h } from './dom.js';

const SVG_NS = 'http://www.w3.org/2000/svg';
let vizUid = 0;
/** A fresh number for the ids of one chart's clip paths, markers, patterns and gradients. */
export function nextVizUid() { return ++vizUid; }
export const r2 = n => Math.round(n * 100) / 100;

/** Build an SVG element. Strings in children become text nodes; attrs never take markup. */
export function svgEl(tag, attrs, ...children) {
  const el = document.createElementNS(SVG_NS, tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === 'class') el.setAttribute('class', Array.isArray(v) ? v.filter(Boolean).join(' ') : v);
      else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, typeof v === 'number' ? String(r2(v)) : String(v));
    }
  }
  appendAll(el, children);
  return el;
}

// Roles: shape carries the role (circle worker, square regulator, triangle disregulator);
// the Okabe-Ito colours fill the glyph through the --role-* tokens in style.css.
export const ROLE_ORDER = ['disregulator', 'worker', 'regulator'];
export const ROLE_PLURAL = { disregulator: 'disregulators', worker: 'workers', regulator: 'regulators' };

/** A role glyph centred on (cx, cy). cls replaces the role fill class (outcome fills in the cascade). */
export function roleGlyph(role, cx, cy, r, cls) {
  const c = ['glyph', cls || 'role-' + (ROLE_ORDER.includes(role) ? role : 'none')];
  if (role === 'regulator') {
    const a = r * 0.88;
    return svgEl('rect', { class: c, x: cx - a, y: cy - a, width: 2 * a, height: 2 * a });
  }
  if (role === 'disregulator') {
    const a = r * 1.22;
    return svgEl('polygon', { class: c, points: `${r2(cx)},${r2(cy - a)} ${r2(cx + a)},${r2(cy + a * 0.75)} ${r2(cx - a)},${r2(cy + a * 0.75)}` });
  }
  return svgEl('circle', { class: c, cx, cy, r });
}

export function roleIcon(role, size = 12) {
  return svgEl('svg', { class: 'role-icon', width: size, height: size, viewBox: `0 0 ${size} ${size}`, 'aria-hidden': 'true', focusable: 'false' },
    roleGlyph(role, size / 2, size / 2 + (role === 'disregulator' ? 0.8 : 0), size / 2 - 1.6));
}

/** One floating tooltip for every chart. Lines are strings or nodes, inserted as text. */
export const tip = (() => {
  let el = null;
  const ensure = () => {
    if (!el) { el = h('div', { class: 'viz-tip', role: 'tooltip', hidden: true }); document.body.appendChild(el); }
    return el;
  };
  return {
    show(ev, lines) {
      const t = ensure();
      t.replaceChildren(...lines.filter(x => x != null && x !== '').map((ln, i) => h('div', { class: i ? 'tip-line' : 'tip-title' }, ln)));
      t.hidden = false;
      const pad = 14;
      const box = t.getBoundingClientRect();
      let x = ev.clientX + pad;
      let y = ev.clientY + pad;
      if (x + box.width > window.innerWidth - 6) x = ev.clientX - box.width - pad;
      if (y + box.height > window.innerHeight - 6) y = ev.clientY - box.height - pad;
      t.style.left = Math.max(4, x) + 'px';
      t.style.top = Math.max(4, y) + 'px';
    },
    hide() { if (el) el.hidden = true; },
  };
})();

/** Attach tooltip listeners to an element; lines() is called on every move. */
export function withTip(el, lines) {
  el.addEventListener('pointermove', ev => tip.show(ev, lines()));
  el.addEventListener('pointerleave', () => tip.hide());
  return el;
}

export function cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

// Colour arithmetic in OKLab, so the single-hue ramp has even perceptual steps.
function hexRgb(hex) {
  const m = /^#([0-9a-f]{6})$/i.exec(String(hex || '').trim());
  if (!m) return [128, 128, 128];
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
const toLin = c => { c /= 255; return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
const fromLin = c => {
  const v = c <= 0.0031308 ? 12.92 * c : 1.055 * Math.max(0, c) ** (1 / 2.4) - 0.055;
  return Math.round(Math.min(1, Math.max(0, v)) * 255);
};
function rgbToOklab([R, G, B]) {
  const r = toLin(R); const g = toLin(G); const b = toLin(B);
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  return [0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s];
}
function oklabToHex([L, a, b]) {
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3;
  const rgb = [4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s].map(fromLin);
  return '#' + rgb.map(c => c.toString(16).padStart(2, '0')).join('');
}
function mixOklab(a, b, t) {
  const A = rgbToOklab(hexRgb(a));
  const B = rgbToOklab(hexRgb(b));
  return oklabToHex(A.map((x, i) => x + (B[i] - x) * t));
}
/** Share judged cheated (0..1) on the single-hue scale of the current colour scheme. */
export function seqColor(frac) { return mixOklab(cssVar('--seq-lo', '#fde8e5'), cssVar('--seq-hi', '#a3170e'), Math.min(1, Math.max(0, frac))); }
/** Ink or white, whichever reads better on a fill. */
export function inkOn(hex) {
  const [r, g, b] = hexRgb(hex);
  const lum = 0.2126 * toLin(r) + 0.7152 * toLin(g) + 0.0722 * toLin(b);
  return lum > 0.179 ? '#1c1c1a' : '#ffffff';
}

export const TIME_STEPS = [0.1, 0.2, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1200, 1800, 3600, 7200];
export const COUNT_STEPS = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000];
export function ticksFor(lo, hi, maxTicks, steps) {
  const span = hi - lo;
  if (!(span > 0)) return [lo];
  const step = steps.find(s => span / s <= maxTicks) || steps[steps.length - 1];
  const out = [];
  for (let v = Math.ceil(lo / step - 1e-9) * step; v <= hi + 1e-9 && out.length < 200; v += step) out.push(+v.toFixed(6));
  return out;
}
export function fmtTick(s) {
  if (s < 60) return `${+s.toFixed(2)}s`;
  const m = Math.floor(s / 60);
  const r = Math.round(s - m * 60);
  if (m < 60) return r ? `${m}m${String(r).padStart(2, '0')}` : `${m}m`;
  return `${Math.floor(m / 60)}h${String(m % 60).padStart(2, '0')}`;
}

/** Legend row: [glyph node, label, title?] items. */
export function vizLegend(items, cls) {
  return h('div', { class: ['viz-legend', cls] }, items.filter(Boolean).map(([glyph, label, title]) =>
    h('span', { class: 'lg-item', title }, glyph, h('span', null, label))));
}
/** A legend glyph 14 px tall: lgSvg(...marks) is 20 px wide, lgSvg(width, ...marks) as wide as asked. */
export function lgSvg(...children) {
  const w = typeof children[0] === 'number' ? children.shift() : 20;
  return svgEl('svg', { class: 'lg-glyph viz', width: w, height: 14, viewBox: `0 0 ${w} 14`, 'aria-hidden': 'true' }, ...children);
}
/** Worker, regulator and disregulator shapes side by side; cls replaces the role fill (outcome fills). */
export function lgRoles(cls) {
  return lgSvg(38, roleGlyph('worker', 6, 7, 3.8, cls), roleGlyph('regulator', 19, 7, 3.8, cls), roleGlyph('disregulator', 32, 7.6, 3.8, cls));
}
export function swatch(cls, title) { return h('span', { class: ['lg-swatch', cls], title, 'aria-hidden': 'true' }); }

/** Red x mark (refusal, blocked call). */
export function xMark(cx, cy, r) {
  return svgEl('path', { class: 'm-x', d: `M${r2(cx - r)},${r2(cy - r)}L${r2(cx + r)},${r2(cy + r)}M${r2(cx + r)},${r2(cy - r)}L${r2(cx - r)},${r2(cy + r)}` });
}
export function diamond(cx, cy, r, cls) {
  return svgEl('path', { class: cls, d: `M${r2(cx)},${r2(cy - r)}L${r2(cx + r)},${r2(cy)}L${r2(cx)},${r2(cy + r)}L${r2(cx - r)},${r2(cy)}Z` });
}

/** Run a few promise jobs at a time. */
export function jobQueue(limit) {
  let active = 0;
  const q = [];
  const pump = () => {
    while (active < limit && q.length) {
      const job = q.shift();
      active++;
      job().finally(() => { active--; pump(); });
    }
  };
  return fn => new Promise((res, rej) => { q.push(() => fn().then(res, rej)); pump(); });
}
