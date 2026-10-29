/* Results browser for ORBIT x CheatBench runs.
 *
 * Vanilla ES2020, no framework, no build step, no network access beyond the local API.
 * Every piece of run data (transcripts, tool output, board posts, reasons, errors) is
 * inserted with textContent or text nodes. Nothing from the API ever reaches innerHTML.
 *
 * Sections
 *   1. DOM helpers and formatting
 *   2. API client
 *   3. Shared components
 *   4. Router, header and breadcrumbs
 *   5. View: runs                    #/runs  (one row per run, opening to its agents)
 *   6. View: run page                #/run/<run>
 *   7. View: transcript              #/run/<run>/episode/<episode>
 *   8. View: context windows         #/run/<run>/context?agent=<name>&call=<i>
 *   9. View: sample                  #/run/<run>/sample
 *  10. Chart helpers (SVG, Canvas, tooltip, colour ramp, ticks)
 *  11. View: timeline                #/run/<run>/timeline (swimlanes, exposure cascade, raster)
 *  12. Views: summary                #/        (cheating by setting, composition grids, team reports)
 *      and swarm visualizations     #/swarm   (grids, funnel, adoption curves, run explorer)
 *  13. Boot
 */
'use strict';
(() => {

// =====================================================================================
// 1. DOM helpers and formatting
// =====================================================================================

const enc = encodeURIComponent;
const DASH = '—';
const REFUSAL = 'I cannot provide that response under the configured defense policy.';
const BOOL_PROPS = new Set(['checked', 'selected', 'disabled', 'hidden', 'open']);

/** Build an element. Strings in children become text nodes; attrs never take HTML. */
function h(tag, attrs, ...children) {
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

function appendAll(el, children) {
  for (const c of children) {
    if (c == null || c === false || c === true) continue;
    if (Array.isArray(c)) appendAll(el, c);
    else el.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

function debounce(fn, ms) {
  let t = null;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

function fmtInt(n) {
  return n == null || Number.isNaN(Number(n)) ? DASH : Number(n).toLocaleString('en-US');
}

function fmtCompact(n) {
  if (n == null || Number.isNaN(Number(n))) return DASH;
  n = Number(n);
  const a = Math.abs(n);
  if (a >= 1e6) return (n / 1e6).toFixed(a >= 1e7 ? 1 : 2) + 'M';
  if (a >= 1e4) return Math.round(n / 1e3) + 'k';
  if (a >= 1e3) return (n / 1e3).toFixed(1) + 'k';
  return String(n);
}

function fmtSecs(s) {
  if (s == null || Number.isNaN(Number(s))) return DASH;
  s = Number(s);
  if (s < 60) return s.toFixed(s < 10 ? 2 : 1) + 's';
  const m = Math.floor(s / 60);
  const r = Math.floor(s % 60);
  if (m < 60) return `${m}m ${String(r).padStart(2, '0')}s`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`;
}

function fmtUsd(x) {
  if (x == null || Number.isNaN(Number(x))) return DASH;
  x = Number(x);
  return '$' + x.toFixed(x < 1 ? 3 : 2);
}

function fmtTime(iso) {
  if (!iso) return DASH;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toISOString().replace('T', ' ').slice(0, 19) + 'Z';
}

function fmtScore(score) {
  if (!score || typeof score !== 'object') return DASH;
  const vals = Object.values(score);
  if (!vals.length) return DASH;
  return vals.map(v => (typeof v === 'number' ? String(+v.toFixed(3)) : String(v))).join(' / ');
}

function firstLine(text, max = 120) {
  const s = String(text == null ? '' : text);
  const line = (s.split('\n').find(l => l.trim()) || '').trim();
  return line.length > max ? line.slice(0, max - 1) + '…' : line;
}

function asText(v) {
  if (v == null) return '';
  return typeof v === 'string' ? v : JSON.stringify(v, null, 2);
}

function plural(n, word, many) {
  return `${fmtInt(n)} ${n === 1 ? word : (many || word + 's')}`;
}

// =====================================================================================
// 2. API client
// =====================================================================================

class ApiError extends Error {
  constructor(path, status, body) {
    super((body && body.error) || `HTTP ${status}`);
    this.path = path;
    this.status = status;
    this.body = body;
  }
}

const API_CACHE_MAX = 250;
const apiCache = new Map();  // path -> Promise, kept in least-recently-used order
// One model call can be hundreds of KB, so single calls get their own small cache.
const CALL_CACHE_MAX = 40;
const callCache = new Map();

function cachedFetch(cache, max, path) {
  if (cache.has(path)) {
    const p = cache.get(path);
    cache.delete(path);
    cache.set(path, p);
    return p;
  }
  const p = fetchJson(path);
  cache.set(path, p);
  p.catch(() => { if (cache.get(path) === p) cache.delete(path); });
  while (cache.size > max) cache.delete(cache.keys().next().value);
  return p;
}

function api(path) { return cachedFetch(apiCache, API_CACHE_MAX, path); }
function apiCall(path) { return cachedFetch(callCache, CALL_CACHE_MAX, path); }
/** Fetch again, replacing any cached copy, so runs and verdicts written since are seen. */
function apiFresh(path) { apiCache.delete(path); return api(path); }

async function fetchJson(path) {
  let res;
  try {
    res = await fetch(path, { headers: { Accept: 'application/json' } });
  } catch (e) {
    throw new ApiError(path, 0, { error: `network error: ${e && e.message}` });
  }
  const text = await res.text();
  let body;
  try {
    body = JSON.parse(text);
  } catch (e) {
    body = { error: `non-JSON response (HTTP ${res.status})`, body: text.slice(0, 4000) };
  }
  if (!res.ok) throw new ApiError(path, res.status, body);
  return body;
}

const P = {
  runs: () => '/api/runs',
  run: r => `/api/runs/${enc(r)}`,
  transcript: (r, ep) => `/api/runs/${enc(r)}/transcript/${enc(ep)}`,
  sample: r => `/api/runs/${enc(r)}/sample`,
  context: r => `/api/runs/${enc(r)}/context`,
  call: (r, i) => `/api/runs/${enc(r)}/context/${i}`,
  timeline: r => `/api/runs/${enc(r)}/timeline`,
  toolFile: (r, tool, file) => `/api/runs/${enc(r)}/tools/${enc(tool)}/` + String(file).split('/').map(enc).join('/'),
  summary: () => '/api/summary',
};

/** Rewrite the hash without navigating; a frame that refuses URL state keeps the view as drawn. */
function setHashQuietly(target) {
  if (location.hash === target) return;
  try {
    history.replaceState(null, '', target);
  } catch (e) {
    /* a sandboxed frame may refuse URL state; the view is already on screen */
  }
}

/** Hash routes. */
const R = {
  summary: () => '#/',
  runs: q => '#/runs' + query(q),
  swarm: q => '#/swarm' + query(q),
  run: r => `#/run/${enc(r)}`,
  episode: (r, ep, q) => `#/run/${enc(r)}/episode/${enc(ep)}` + query(q),
  context: (r, q) => `#/run/${enc(r)}/context` + query(q),
  sample: r => `#/run/${enc(r)}/sample`,
  timeline: r => `#/run/${enc(r)}/timeline`,
};

function query(q) {
  if (!q) return '';
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(q)) if (v != null && v !== '') p.set(k, String(v));
  const s = p.toString();
  return s ? '?' + s : '';
}

// =====================================================================================
// 3. Shared components
// =====================================================================================

function badge(text, kind, title) {
  return h('span', { class: ['badge', kind ? 'b-' + kind : 'b-plain'], title }, text);
}

function muted(text, cls) {
  return h('span', { class: ['muted', cls] }, text);
}

function mono(text) {
  return h('span', { class: 'mono' }, text == null || text === '' ? DASH : text);
}

function link(href, text, cls) {
  return h('a', { href, class: cls }, text);
}

/** yes/no badge for a verdict boolean; null renders a dash. */
function yn(v, yesKind, noKind) {
  if (v === true) return badge('yes', yesKind || 'plain');
  if (v === false) return badge('no', noKind || 'plain');
  return muted(DASH);
}

/** Text in a <pre>; longer than `lines` lines (or `chars` chars) starts collapsed. */
function longText(text, opts = {}) {
  const { lines = 30, chars = 12000, cls = '' } = opts;
  const full = text == null ? '' : String(text);
  const all = full.split('\n');
  const pre = h('pre', { class: ['pre', cls] });
  if (all.length <= lines && full.length <= chars) {
    pre.textContent = full;
    return pre;
  }
  let preview = all.slice(0, lines).join('\n');
  if (preview.length > chars) preview = preview.slice(0, chars);
  const wrap = h('div', { class: 'longtext' });
  const btn = h('button', { type: 'button', class: 'expand' });
  let expanded = false;
  const sync = () => {
    pre.textContent = expanded ? full : preview + '\n…';
    btn.textContent = expanded
      ? 'collapse'
      : `expand: ${plural(all.length, 'line')}, ${fmtInt(full.length)} chars`;
    wrap.classList.toggle('collapsed', !expanded);
  };
  btn.addEventListener('click', () => { expanded = !expanded; sync(); });
  sync();
  return appendAll(wrap, [pre, btn]);
}

/** <details> whose body is built on first open. */
function lazyDetails(summary, build, opts = {}) {
  const { open = false, cls = '' } = opts;
  const d = h('details', { class: cls });
  d.appendChild(h('summary', null, summary));
  let built = false;
  const ensure = () => {
    if (built) return;
    built = true;
    appendAll(d, [build()]);
  };
  d.addEventListener('toggle', () => { if (d.open) ensure(); });
  if (open) { ensure(); d.open = true; }
  return d;
}

// Gemini appends an opaque base64 "thought signature" to its reasoning summary, after a blank
// line (or as the whole reasoning). It is not readable text, so it is split off and hidden.
const SIG_RE = /(?:^|\n\s*\n)\s*([A-Za-z0-9+/_-]{100,}={0,2})\s*$/;

function splitSignature(raw) {
  const s = raw == null ? '' : String(raw);
  const m = SIG_RE.exec(s);
  if (!m || !/[A-Z]/.test(m[1]) || !/[a-z]/.test(m[1]) || !/[0-9]/.test(m[1])) return { text: s, sig: '' };
  return { text: s.slice(0, m.index).replace(/\s+$/, ''), sig: m[1] };
}

function reasoningView(raw, lines) {
  const { text, sig } = splitSignature(raw);
  const label = text
    ? `reasoning (${fmtInt(text.length)} chars${sig ? `; ${fmtInt(sig.length)}-char opaque signature hidden` : ''})`
    : `reasoning: opaque provider signature only (${fmtInt(sig.length)} chars, no readable text)`;
  return lazyDetails(label, () => h('div', null,
    text ? longText(text, { lines }) : null,
    sig ? lazyDetails('show the opaque signature', () => longText(sig, { lines: 4, chars: 400, cls: 'sig' }), { cls: 'sub' }) : null),
  { cls: 'reasoning' });
}

/** A link into a view that reads the eval log, or a disabled button when the log is missing. */
function logLink(ok, evalLog, href, label, cls) {
  if (ok) return link(href, label, cls);
  return h('span', { class: [cls, 'disabled'], title: evalLog ? `eval log missing: ${evalLog}` : 'no eval log recorded for this run' }, label);
}

/** Definition list from [key, value] pairs; undefined values are skipped. */
function kv(pairs, cls) {
  const dl = h('dl', { class: ['kv', cls] });
  for (const [k, v] of pairs) {
    if (v === undefined) continue;
    dl.appendChild(h('div', { class: 'kv-item' }, h('dt', null, k), h('dd', null, v == null || v === '' ? muted(DASH) : v)));
  }
  return dl;
}

function section(title, ...children) {
  return h('section', { class: 'card' }, h('h2', null, title), ...children);
}

function errorBox(err, context) {
  const isApi = err instanceof ApiError;
  const body = isApi ? err.body : { error: String((err && err.message) || err) };
  const title = isApi
    ? `API error${err.status ? ' ' + err.status : ''} on ${err.path}`
    : 'Error';
  const tb = body && typeof body.traceback === 'string' ? body.traceback : null;
  const shown = tb ? Object.assign({}, body, { traceback: '(below)' }) : body;
  return h('div', { class: 'error-box' },
    h('div', { class: 'error-title' }, context ? `${context}: ${title}` : title),
    h('pre', { class: 'pre' }, JSON.stringify(shown, null, 2)),
    tb ? h('pre', { class: 'pre traceback' }, tb) : null,
    !isApi && err && err.stack ? h('pre', { class: 'pre traceback' }, String(err.stack)) : null);
}

function select(options, value, onChange, attrs) {
  const s = h('select', attrs,
    options.map(o => (Array.isArray(o) ? h('option', { value: o[0] }, o[1]) : h('option', { value: o }, o))));
  s.value = value;
  s.addEventListener('change', () => onChange(s.value));
  return s;
}

function labeled(label, control) {
  return h('label', { class: 'field' }, h('span', { class: 'field-label' }, label), control);
}

/** Tool-call arguments. Bash commands and Python code render as code blocks. */
function argsView(fn, args) {
  let a = args;
  if (typeof a === 'string') {
    try {
      a = JSON.parse(a);
    } catch (e) {
      return h('div', { class: 'args' }, longText(args, { cls: 'code' }));
    }
  }
  if (a == null) return h('div', { class: 'args' }, muted('(no arguments)', 'small'));
  if (typeof a !== 'object' || Array.isArray(a)) {
    return h('div', { class: 'args' }, h('pre', { class: 'pre code' }, JSON.stringify(a, null, 2)));
  }
  const box = h('div', { class: 'args' });
  const simple = [];
  const keys = Object.keys(a);
  if (!keys.length) return appendAll(box, [muted('(no arguments)', 'small')]);
  for (const k of keys) {
    const v = a[k];
    const codeLang = typeof v === 'string'
      ? (k === 'command' || k === 'cmd' ? 'bash' : k === 'code' ? (fn === 'bash' ? 'bash' : 'python') : null)
      : null;
    if (codeLang) {
      box.appendChild(h('div', { class: 'arg' },
        h('div', { class: 'arg-key' }, k, ' ', muted(codeLang, 'small')),
        longText(v, { lines: 40, cls: `code lang-${codeLang}` })));
    } else if (typeof v === 'string' && (v.includes('\n') || v.length > 100)) {
      box.appendChild(h('div', { class: 'arg' },
        h('div', { class: 'arg-key' }, k),
        longText(v, { lines: 20, cls: 'argtext' })));
    } else {
      simple.push(`${k}: ${JSON.stringify(v)}`);
    }
  }
  if (simple.length) box.insertBefore(h('pre', { class: 'pre code args-simple' }, simple.join('\n')), box.firstChild);
  return box;
}

function isBlockedResult(rx) {
  return !!rx && typeof rx.error === 'string' && rx.error.startsWith('Blocked by defense');
}

/** Small badges for extra keys that the renderer does not handle itself. */
function extraBadges(extra, skip) {
  if (!extra || typeof extra !== 'object') return null;
  const out = [];
  for (const [k, v] of Object.entries(extra)) {
    if (skip.includes(k) || v == null || v === false || v === '') continue;
    const label = v === true ? k : `${k}=${typeof v === 'string' ? v : JSON.stringify(v)}`;
    out.push(badge(label.length > 60 ? label.slice(0, 59) + '…' : label, 'plain', label));
  }
  return out;
}

// Colours of the verdict flags we know: [field, yes kind, no kind]. The server passes every field of the
// judge's row, so any other boolean field shows as a plain flag and any other text field as a note.
const VERDICT_FIELDS = [
  ['cheated', 'bad', 'good'],
  ['honey_pots_seen', 'warn', ''],
  ['attempted_access', 'bad', 'good'],
  ['extracted_submission', 'bad', ''],
  ['success_claim_seen_before_access', 'warn', ''],
  ['disclosed_access', 'good', ''],
  ['explicit_prohibition_violated', 'bad', ''],
  ['reference_solution_seeking', 'warn', ''],
  ['reference_solution_obtained', 'bad', ''],
  ['reference_solution_used', 'bad', 'good'],
  ['documentation_lookup', 'plain', ''],
  ['evaluation_tampering', 'bad', ''],
];
const VERDICT_META = ['summary', 'judge', 'judge_cost_usd', 'parse_ok', 'judge_error', 'raw'];
// Field names whose label is not just the underscores replaced.
const VERDICT_LABELS = { honey_pots_seen: 'honeypots seen' };
const verdictLabel = k => VERDICT_LABELS[k] || k.replace(/_/g, ' ');

function verdictBlock(v) {
  if (!v) return h('div', { class: 'verdict' }, muted('Not judged.'));
  const known = new Map(VERDICT_FIELDS.map(([k, yk, nk]) => [k, [yk, nk]]));
  const flags = Object.keys(v).filter(k => known.has(k) || typeof v[k] === 'boolean').filter(k => !VERDICT_META.includes(k));
  flags.sort((a, b) => (known.has(a) ? 0 : 1) - (known.has(b) ? 0 : 1));
  const notes = Object.entries(v).filter(([k, x]) => !VERDICT_META.includes(k) && !flags.includes(k) && x != null && x !== '');
  return h('div', { class: 'verdict' },
    h('div', { class: 'verdict-flags' },
      flags.map(k => {
        const [yk, nk] = known.get(k) || ['plain', 'plain'];
        return h('span', { class: 'vf' }, h('span', { class: 'vf-label' }, verdictLabel(k)), yn(v[k], yk, nk));
      })),
    notes.length ? kv(notes.map(([k, x]) => [verdictLabel(k), asText(x)]), 'kv-grid') : null,
    h('div', { class: 'verdict-meta' },
      muted(`judge ${v.judge || '?'} · ${fmtUsd(v.judge_cost_usd)}`),
      v.parse_ok === false ? badge('judge output did not parse', 'bad') : null,
      v.judge_error ? badge('judge error', 'bad', String(v.judge_error)) : null),
    v.judge_error ? h('pre', { class: 'pre errtext' }, String(v.judge_error)) : null,
    v.raw ? lazyDetails('raw judge output (did not parse)', () => longText(v.raw, { lines: 40 }), { cls: 'sub' }) : null,
    v.summary ? h('p', { class: 'verdict-summary' }, v.summary) : null);
}

// The cheating ladder (src/benchmark/ladder.py): the rung the judge found and the calls and rounds it cites.
const RUNGS = ['exposed', 'tried', 'opened', 'used', 'shipped'];
const RUNG_KIND = { none: 'good', exposed: 'warn', tried: 'bad', opened: 'bad', used: 'bad', shipped: 'bad' };
const BESIDE_LADDER = [['searched', 'warn'], ['tampered', 'bad'], ['admitted', 'good'], ['concealed', 'bad'], ['false_denial', 'bad']];

function rungBadge(rung) {
  return badge(rung, RUNG_KIND[rung] || 'plain', 'the highest rung of the cheating ladder the judge found');
}

function ladderBlock(l) {
  if (!l) return null;
  if (l.parse_ok === false) {
    return h('div', { class: 'verdict' }, muted('Ladder: the judge output did not parse. '),
      l.judge_error ? badge('judge error', 'bad', String(l.judge_error)) : null);
  }
  const where = f => (l[f + '_call'] == null ? null
    : `call ${l[f + '_call']}` + (l[f + '_round'] != null ? ` (round ${l[f + '_round']})` : ''));
  const rungs = RUNGS.map(f => [f, h('span', null, yn(l[f], 'bad', 'good'), where(f) ? [' ', muted(where(f), 'small')] : null)]);
  const beside = BESIDE_LADDER.map(([f, kind]) => [f.replace('_', ' '), h('span', null, yn(l[f], kind, ''),
    f === 'searched' && where(f) ? [' ', muted(where(f), 'small')] : null)]);
  const cites = !l.citations ? null : l.citations.ok ? badge('cited calls exist', 'good', JSON.stringify(l.citations))
    : badge('a cited call is missing', 'warn', JSON.stringify(l.citations));
  const evidence = [['tried', l.tried_evidence], ['opened', l.opened_evidence], ['used', l.used_evidence],
    ['shipped', l.shipped_evidence], ['tampered', l.tampered_evidence], ['honesty', l.honesty_evidence]].filter(([, t]) => t);
  return h('div', { class: 'verdict' },
    h('div', { class: 'verdict-flags' }, h('span', { class: 'vf' }, h('span', { class: 'vf-label' }, 'rung'), rungBadge(l.rung))),
    kv([...rungs, ...beside, ['exposed source', l.exposed_source], ['view', l.view]], 'kv-grid'),
    h('div', { class: 'verdict-meta' }, muted(`ladder judge ${l.judge || '?'} · ${fmtUsd(l.judge_cost_usd)}`), ' ', cites),
    l.ladder_summary ? h('p', { class: 'verdict-summary' }, l.ladder_summary) : null,
    evidence.map(([k, t]) => lazyDetails(`${k} evidence`, () => longText(t, { lines: 20 }), { cls: 'sub' })));
}

// The code ladder's row of an episode (signals.jsonl, src/measures/signals.py): the rungs by code, no judge.
function signalsBlock(s) {
  if (!s) return null;
  const where = f => (s[f + '_call'] == null ? null : `call ${s[f + '_call']}`);
  const rungs = RUNGS.map(f => [f, h('span', null, yn(s[f], 'bad', 'good'), where(f) ? [' ', muted(where(f), 'small')] : null)]);
  const evidence = [['tried', s.tried_evidence], ['opened', s.opened_evidence], ['used', s.used_evidence],
    ['shipped', s.shipped_evidence]].filter(([, t]) => t);
  return h('div', { class: 'verdict' },
    h('div', { class: 'verdict-flags' }, h('span', { class: 'vf' }, h('span', { class: 'vf-label' }, 'code rung'), rungBadge(s.rung))),
    kv([...rungs, ['searched', yn(s.searched, 'warn', '')], ['exposed source', s.exposed_source],
      ['probe', s.probe ? 'ran' : 'none'], ['lines seen', s.lines_seen]], 'kv-grid'),
    h('div', { class: 'verdict-meta' }, muted('code ladder: the transcript and the sandbox probe, no judge')),
    evidence.map(([k, t]) => lazyDetails(`${k} evidence`, () => longText(t, { lines: 20 }), { cls: 'sub' })));
}

function episodeRecord(ep) {
  const u = ep.usage || {};
  const d = ep.defense;
  return kv([
    ['agent', ep.orbit_agent || ep.agent],
    ['hidden role', ep.role ? roleBadge(ep.role) : undefined],
    ['tool calls', fmtInt(ep.n_tool_calls)],
    ['turns', fmtInt(ep.n_turns)],
    ['tokens prompt', h('span', { title: fmtInt(u.input) }, fmtCompact(u.input))],
    ['tokens completion', h('span', { title: fmtInt(u.output) }, fmtCompact(u.output))],
    ['tokens cache read', h('span', { title: fmtInt(u.cache_read) }, fmtCompact(u.cache_read))],
    ['wall time', fmtSecs(ep.wall_time)],
    ['limit', ep.limit == null ? null : asText(ep.limit)],
    ['ended by', ep.ended_by == null ? null : (ep.ended_by === 'submit' ? badge('submit', 'good', 'the agent called submit before the limit hit the others') : asText(ep.ended_by))],
    ['failure', ep.failure ? badge(ep.failure, 'bad') : null],
    ['ok', ep.ok == null ? null : yn(ep.ok, 'good', 'bad')],
    ['score', fmtScore(ep.score)],
    ['control', ep.control || undefined],
    ['defenses', ep.defenses && ep.defenses.length ? ep.defenses.join(', ') : undefined],
    ['defense checks / blocks', d ? defenseCounts(d) : undefined],
  ], 'kv-grid');
}

function defenseCounts(d) {
  const parts = [`${plural(d.checks || 0, 'check')}, ${plural(d.blocks || 0, 'block')}`];
  const bb = d.blocks_by_boundary || {};
  const cb = d.checks_by_boundary || {};
  const bounds = [...new Set([...Object.keys(cb), ...Object.keys(bb)])];
  const detail = bounds.map(b => `${b} ${fmtInt(bb[b] || 0)}/${fmtInt(cb[b] || 0)}`).join(' · ');
  return h('span', null, parts[0], d.blocks ? ' ' : '', d.blocks ? badge('blocked', 'blocked') : null,
    detail ? h('div', { class: 'muted small' }, 'blocks/checks: ', detail) : null);
}

// =====================================================================================
// 4. Router, header and breadcrumbs
// =====================================================================================

// Cleanups of the route on screen. Each route gets its own list, so a view that finishes
// loading after the user has left cannot leave a listener behind on the next view.
let cleanups = [];
let routeToken = 0;

function runCleanups(list) {
  for (const fn of list.splice(0)) { try { fn(); } catch (e) { /* ignore */ } }
}

function parseHash() {
  const raw = location.hash.replace(/^#/, '') || '/';
  const qi = raw.indexOf('?');
  const pathPart = qi >= 0 ? raw.slice(0, qi) : raw;
  const q = new URLSearchParams(qi >= 0 ? raw.slice(qi + 1) : '');
  let parts;
  try {
    parts = pathPart.split('/').filter(Boolean).map(decodeURIComponent);
  } catch (e) {
    return { view: 'notfound', raw };
  }
  if (!parts.length) return { view: 'summary', query: q };
  if (parts[0] === 'runs' && parts.length === 1) return { view: 'runs', query: q };
  if (parts[0] === 'swarm' && parts.length === 1) return { view: 'swarm', query: q };
  if (parts[0] === 'overview' && parts.length === 1) return { view: 'summary', query: q };
  if (parts[0] === 'run' && parts.length >= 2) {
    const run = parts[1];
    if (parts.length === 2) return { view: 'run', run, query: q };
    if (parts[2] === 'episode' && parts.length >= 4) {
      return { view: 'episode', run, episode: parts.slice(3).join('/'), query: q };
    }
    if (parts[2] === 'context' && parts.length === 3) return { view: 'context', run, query: q };
    if (parts[2] === 'sample' && parts.length === 3) return { view: 'sample', run, query: q };
    if (parts[2] === 'timeline' && parts.length === 3) return { view: 'timeline', run, query: q };
  }
  return { view: 'notfound', raw };
}

function renderShell() {
  const app = document.getElementById('app');
  app.replaceChildren(
    h('button', { type: 'button', class: 'skip-link', onclick: () => { const m = document.getElementById('main'); if (m) m.focus(); } }, 'Skip to content'),
    h('header', { class: 'topbar' },
      h('a', { class: 'brand', href: '#/' }, 'CheatBench × ORBIT'),
      h('nav', { class: 'nav-tabs', 'aria-label': 'Sections' },
        h('a', { class: 'nav-tab', id: 'nav-summary', href: '#/' }, 'Summary'),
        h('a', { class: 'nav-tab', id: 'nav-runs', href: '#/runs' }, 'Runs'),
        h('a', { class: 'nav-tab', id: 'nav-swarm', href: '#/swarm' }, 'Swarm')),
      h('nav', { class: 'crumbs', id: 'crumbs', 'aria-label': 'Breadcrumbs' }),
      h('button', {
        type: 'button', class: 'btn btn-small', title: 'Clear cached API responses and reload this view',
        onclick: () => { apiCache.clear(); callCache.clear(); route(); },
      }, 'Reload')),
    h('main', { id: 'main', tabindex: '-1' }));
}

function setCrumbs(r) {
  const nav = document.getElementById('crumbs');
  const items = [];
  const sep = () => h('span', { class: 'sep' }, '/');
  const sectionOf = { summary: 'summary', runs: 'runs', swarm: 'swarm' }[r.view] || (r.run ? 'runs' : null);
  for (const [id, key] of [['nav-summary', 'summary'], ['nav-runs', 'runs'], ['nav-swarm', 'swarm']]) {
    const el = document.getElementById(id);
    if (!el) continue;
    el.classList.toggle('active', sectionOf === key);
    if (sectionOf === key) el.setAttribute('aria-current', 'page'); else el.removeAttribute('aria-current');
  }
  if (r.run) {
    items.push(link(R.runs(), 'Runs', 'crumb'), sep());
    items.push(r.view === 'run' ? h('span', { class: 'crumb current' }, r.run) : link(R.run(r.run), r.run, 'crumb'));
    if (r.view === 'episode') items.push(sep(), h('span', { class: 'crumb current' }, `transcript ${r.episode}`));
    if (r.view === 'context') items.push(sep(), h('span', { class: 'crumb current' }, 'context windows'));
    if (r.view === 'sample') items.push(sep(), h('span', { class: 'crumb current' }, 'sample'));
    if (r.view === 'timeline') items.push(sep(), h('span', { class: 'crumb current' }, 'timeline'));
    const tab = (href, label, view, needsLog) => h('a', {
      href, class: ['tab', r.view === view && 'active'], dataset: needsLog ? { needsLog: '1' } : null,
    }, label);
    items.push(h('span', { class: 'tabs' },
      tab(R.run(r.run), 'run', 'run', false),
      tab(R.timeline(r.run), 'timeline', 'timeline', false),
      tab(R.context(r.run), 'context', 'context', true),
      tab(R.sample(r.run), 'sample', 'sample', true)));
  }
  nav.replaceChildren(...items);
}

/** Context windows and the sample come from the eval log; grey those tabs out when it is missing. */
function markEvalLogTabs(r, token) {
  if (!r.run) return;
  api(P.runs()).then(runs => {
    if (token !== routeToken) return;
    const rs = (runs || []).find(x => x.run === r.run);
    if (!rs || rs.eval_log_exists) return;
    for (const a of document.querySelectorAll('#crumbs .tab[data-needs-log]')) {
      if (a.classList.contains('active')) continue;
      a.replaceWith(h('span', { class: 'tab disabled', title: 'eval log missing: no context windows or sample for this run' }, a.textContent));
    }
  }).catch(() => {});
}

const VIEWS = {
  summary: viewSummary,
  runs: viewRuns,
  swarm: viewSwarm,
  run: viewRun,
  episode: viewEpisode,
  context: viewContext,
  sample: viewSample,
  timeline: viewTimeline,
  notfound: async (r, view) => {
    view.mounted(() => { document.title = 'Unknown page · results'; });
    return h('div', { class: 'page' },
      h('h1', null, 'Unknown page'),
      h('p', null, 'No view matches ', mono('#' + (r.raw || '')), '. '),
      link('#/', 'Back to the summary'));
  },
};

let keepScrollOnce = false;   // set by a theme change: the view is drawn again without jumping to the top

/**
 * Render the view for the current hash. A view is `async (route, view) => Node`.
 * view.mounted(fn) runs fn once the node is on screen, and only if the user is still on
 * this route. view.cleanup(fn) runs fn when the user leaves. Views keep every global side
 * effect (document.title, history.replaceState, document listeners) behind these two.
 */
async function route() {
  runCleanups(cleanups);
  tip.hide();
  const mine = [];
  cleanups = mine;
  const r = parseHash();
  const token = ++routeToken;
  setCrumbs(r);
  markEvalLogTabs(r, token);
  const main = document.getElementById('main');
  main.replaceChildren(h('div', { class: 'loading' }, 'Loading…'));
  const mounted = [];
  const view = { mounted: fn => mounted.push(fn), cleanup: fn => mine.push(fn) };
  try {
    const node = await VIEWS[r.view](r, view);
    if (token !== routeToken) { runCleanups(mine); return; }
    const y = keepScrollOnce ? window.scrollY : 0;
    keepScrollOnce = false;
    main.replaceChildren(node);
    window.scrollTo(0, y);
    for (const fn of mounted) fn();
  } catch (e) {
    if (token !== routeToken) { runCleanups(mine); return; }
    document.title = 'Error · results';
    main.replaceChildren(h('div', { class: 'page' }, errorBox(e)));
  }
}

// =====================================================================================
// 5. View: runs   #/runs   (one row per run; a row opens to its agents)
// =====================================================================================

const runsState = { q: '', setting: '', control: '', cheated: '', failed: 'hide', sortKey: 'setting', sortDir: 1 };
const openRuns = new Set();   // runs whose agent rows are open, kept across visits

/**
 * The arrangement a run was made in: the communication graph (a lone agent, or every agent on one
 * shared board), the roster size, and whether roles were hidden. run.json calls a board with hidden
 * roles a "swarm"; the graph is the same board, so the setting is read from graph, n_agents and swarm.
 */
/** The tasks with a setting of their own: the key's prefix, the label and the sort order. */
const OWN_SETTING = { knowledge_work: ['kw', 'knowledge work', 4], subset_sum: ['subset', 'subset sum', 5],
  prime_factorization: ['factor', 'prime factorization', 6], protein_design: ['protein', 'protein design', 7] };

function settingOf(run) {
  const graph = run.graph || (run.topology === 'single' ? 'single' : 'board');
  const n = run.n_agents || (run.agents || []).length || (run.episodes || []).length;
  const sw = run.swarm || null;
  if (run.swe) return sweSetting(run.swe, n);
  if (OWN_SETTING[run.task]) {
    // these tasks are their own settings, apart from openmath, by team size and hidden roles
    const [prefix, label, order] = OWN_SETTING[run.task];
    const n1 = graph === 'single' ? 1 : n;
    const hidden = !!(sw && sw.composition);
    return { key: `${prefix}-${hidden ? 'roles' : 'board'}-${n1}`, order, n: n1,
      label: `${label} ${n1 === 1 ? 'lone agent' : `board of ${n1}`}${hidden ? ', hidden roles' : ''}`,
      ...(hidden ? { composition: sw.composition, R: sw.regulators, D: sw.disregulators, W: sw.workers } : {}) };
  }
  if (graph === 'single') return { key: 'single', label: 'lone agent', order: 0, n: 1 };
  if (sw && sw.composition) {
    return { key: `roles-${n}`, label: `board of ${n}, hidden roles`, order: 2, n,
      composition: sw.composition, R: sw.regulators, D: sw.disregulators, W: sw.workers };
  }
  return { key: `board-${n}`, label: `board of ${n}`, order: 1, n };
}

/** A CheatBench SWE run: its own setting, apart from the openmath runs, by issue, team size, honeypot and hint. */
function sweSetting(swe, n0) {
  const n = swe.n_agents || n0;
  const honeypot = swe.honeypot && swe.honeypot !== 'as_released' ? swe.honeypot : null;
  const bits = [honeypot, swe.rule && swe.rule !== 'none' ? `rule: ${swe.rule}` : null].filter(Boolean);
  const base = `SWE ${swe.task_key || ''} ${n === 1 ? 'lone agent' : `board of ${n}`}`.replace(/\s+/g, ' ');
  return { key: `swe-${swe.task_key}-${swe.arm}`, label: base + (bits.length ? ` (${bits.join(', ')})` : ''), order: 3, n };
}

function settingSortKey(s, run) {
  const pad = x => String(x == null ? 0 : x).padStart(3, '0');
  return `${s.order}|${pad(s.n)}|${pad(s.R)}|${pad(s.D)}|${run}`;
}

/** Counts of one run from its episodes; a verdict counts only with a transcript (the rule of /api/summary). */
function runAggregate(run) {
  const eps = run.episodes || [];
  const judged = eps.filter(e => e.verdict && e.verdict.cheated != null && e.has_transcript);
  const n = k => judged.filter(e => e.verdict[k]).length;
  const sum = f => eps.reduce((s, e) => s + (Number(f(e)) || 0), 0);
  const endings = run.endings || {};
  const d = run.deliverables || null;
  return {
    agents: eps.length, judged: judged.length, cheated: n('cheated'), seen: n('honey_pots_seen'),
    attempted: n('attempted_access'), extracted: n('extracted_submission'), disclosed: n('disclosed_access'),
    blocks: sum(e => (e.defense || {}).blocks), checks: sum(e => (e.defense || {}).checks), hasDefense: eps.some(e => e.defense),
    calls: sum(e => e.n_tool_calls), wall: Math.max(0, ...eps.map(e => e.wall_time || 0)),
    prompt: sum(e => (e.usage || {}).input), judgeCost: sum(e => (e.verdict || {}).judge_cost_usd),
    endings,
    ended: Object.entries(endings).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([k, c]) => `${c} ${k}`).join(', '),
    registered: d ? d.registered_on_main : null, copied: d ? d.registered_message_copied_from : null,
    report: run.swarm ? run.swarm.swarm_report_cheated : undefined, hasReport: run.swarm ? run.swarm.has_report : undefined,
  };
}

function cmpNullsLast(a, b, dir) {
  const an = a == null || a === '';
  const bn = b == null || b === '';
  if (an && bn) return 0;
  if (an) return 1;
  if (bn) return -1;
  if (typeof a === 'boolean') a = +a;
  if (typeof b === 'boolean') b = +b;
  if (typeof a === 'number' && typeof b === 'number') return (a - b) * dir;
  return String(a).localeCompare(String(b), 'en', { numeric: true }) * dir;
}

const RUN_COLS = [
  { key: 'run', label: 'Run', sort: r => r.run.run },
  { key: 'setting', label: 'Setting', sort: r => settingSortKey(r.setting, r.run.run), title: 'graph, roster size and hidden roles' },
  { key: 'control', label: 'Control', sort: r => r.run.control },
  { key: 'agents', label: 'Agents', num: true, sort: r => r.agg.agents },
  { key: 'cheated', label: 'Cheated', num: true, sort: r => (r.agg.judged ? r.agg.cheated / r.agg.judged : null), title: 'agents the judge found cheated, over judged agents' },
  { key: 'seen', label: 'Saw honeypot', num: true, sort: r => r.agg.seen, title: 'judged agents that saw the honeypot' },
  { key: 'attempted', label: 'Attempted', num: true, sort: r => r.agg.attempted, title: 'judged agents that attempted access' },
  { key: 'report', label: 'Team report', sort: r => r.agg.report, title: 'what swarm_report.txt says' },
  { key: 'registered', label: 'Registered', sort: r => r.agg.registered, title: 'a registry entry on main at the end' },
  { key: 'ended', label: 'Ended', sort: r => r.agg.ended, title: 'how the agents’ episodes ended' },
  { key: 'blocks', label: 'Blocks / checks', num: true, sort: r => (r.agg.hasDefense ? r.agg.blocks : null) },
  { key: 'wall', label: 'Wall', num: true, sort: r => r.agg.wall },
  { key: 'prompt', label: 'Prompt tok', num: true, sort: r => r.agg.prompt },
  { key: 'judge', label: 'Judge $', num: true, sort: r => r.agg.judgeCost },
];

/**
 * Runs-list state from the URL: #/runs?q=&setting=&control=&cheated=&failed=1|only&sort=&dir=desc.
 * A bare #/runs keeps the state from earlier in the session; #/runs?all=1 resets every filter.
 */
function runsStateFromQuery(q, settings, controls) {
  if (!q || ![...q.keys()].length) return;
  const pick = (v, allowed) => (allowed.includes(v) ? v : '');
  runsState.q = q.get('q') || '';
  runsState.setting = pick(q.get('setting') || '', settings);
  runsState.control = pick(q.get('control') || '', controls);
  runsState.cheated = pick(q.get('cheated') || '', ['yes', 'no', 'none']);
  const f = q.get('failed');
  runsState.failed = f === '1' || f === 'show' ? 'show' : f === 'only' ? 'only' : 'hide';
  const sk = q.get('sort');
  runsState.sortKey = RUN_COLS.some(c => c.key === sk) ? sk : 'setting';
  runsState.sortDir = q.get('dir') === 'desc' ? -1 : 1;
}

function runsQuery() {
  const s = runsState;
  return {
    q: s.q.trim() ? s.q : '', setting: s.setting, control: s.control, cheated: s.cheated,
    failed: s.failed === 'show' ? '1' : s.failed === 'only' ? 'only' : '', sort: s.sortKey === 'setting' ? '' : s.sortKey, dir: s.sortDir < 0 ? 'desc' : '',
  };
}

// Hidden roles: Okabe-Ito colour plus shape (circle worker, square regulator, triangle disregulator).
// Red and green stay reserved for outcomes.
function roleBadge(role) {
  return h('span', { class: 'badge b-plain', title: 'hidden role in this swarm run' },
    ROLE_ORDER.includes(role) ? roleIcon(role, 10) : null, role);
}

/** A verdict counts only on an episode with a transcript (the rule of /api/summary); others are drawn grey. */
function verdictCell(ep, yesKind = 'bad', noKind = 'good') {
  const v = ep && ep.verdict ? ep.verdict.cheated : null;
  if (v != null && !ep.has_transcript) {
    return h('span', { title: 'verdict on an episode with no transcript (harness crash or provider error); not counted in the summary' }, yn(v, 'plain', 'plain'));
  }
  return yn(v, yesKind, noKind);
}

function endedCell(ep) {
  if (ep.failure) {
    const limit = ep.failure === 'timeout' || ep.failure === 'max_turns';
    return badge(ep.failure, limit ? 'warn' : 'bad', limit ? 'the sample hit this limit before the agent submitted' : (ep.error ? firstLine(ep.error, 300) : ''));
  }
  if (ep.ended_by === 'submit') return badge('submit', 'good', 'the agent called submit');
  return muted(ep.ended_by || DASH);
}

function registeredCell(agg) {
  if (agg.registered == null) return muted(DASH, 'small');
  if (!agg.registered) return badge('no', 'plain');
  return h('span', null, badge('yes', 'good'),
    agg.copied ? [' ', badge('copied', 'warn', `the registry entry carries ${agg.copied}’s message verbatim`)] : null);
}

function reportCell(agg) {
  if (agg.hasReport === undefined) return muted(DASH, 'small');
  if (!agg.hasReport) return badge('no file', 'warn');
  if (agg.report == null) return badge('unparsed', 'warn');
  const agrees = agg.judged ? (agg.report === (agg.cheated > 0)) : null;
  return h('span', { title: agrees == null ? 'no judged agent to compare with' : agrees ? 'agrees with the judge' : 'disagrees with the judge' },
    yn(agg.report, 'bad', 'good'), agrees === false ? [' ', badge('≠ judge', 'warn', 'the report disagrees with the judge')] : null);
}

/** The agents of one run, one row each. */
function episodesTable(run) {
  const eps = run.episodes || [];
  const cols = [['agent'], ['role'], ['calls', 'num'], ['ended by'], ['cheated'], ['rung'], ['saw honeypot'], ['attempted'], ['extracted'], ['disclosed'],
    ['prompt tok', 'num'], ['judge $', 'num'], ['links', 'hidden-name']];
  return h('table', { class: 'data compact eps-table' },
    h('thead', null, h('tr', null, cols.map(([c, cls]) => h('th', { class: cls === 'hidden-name' ? '' : cls, scope: 'col' }, cls === 'hidden-name' ? h('span', { class: 'visually-hidden' }, c) : c)))),
    h('tbody', null, eps.map(ep => {
      const v = ep.verdict || null;
      const u = ep.usage || {};
      const name = ep.orbit_agent || ep.episode;
      return h('tr', null,
        h('td', null, ep.has_transcript ? link(R.episode(run.run, ep.episode), name, 'ep-link')
          : h('span', { title: 'no transcript on disk' }, name, ' ', muted('(no transcript)', 'small'))),
        h('td', null, ep.role ? roleBadge(ep.role) : muted(DASH)),
        h('td', { class: 'num', title: `${fmtInt(ep.n_turns)} turns` }, fmtInt(ep.n_tool_calls)),
        h('td', null, endedCell(ep)),
        h('td', null, verdictCell(ep), v && (v.parse_ok === false || v.judge_error) ? [' ', badge('judge issue', 'warn', String(v.judge_error || 'parse failed'))] : null),
        h('td', null, ep.ladder && ep.ladder.rung ? rungBadge(ep.ladder.rung) : muted(DASH)),
        h('td', null, yn(v ? v.honey_pots_seen : null, 'warn')),
        h('td', null, yn(v ? v.attempted_access : null, 'bad', 'good')),
        h('td', null, yn(v ? v.extracted_submission : null, 'bad')),
        h('td', null, yn(v ? v.disclosed_access : null, 'good')),
        h('td', { class: 'num', title: `${fmtInt(u.input)} prompt, ${fmtInt(u.cache_read)} cache read, ${fmtInt(u.output)} completion` }, fmtCompact(u.input)),
        h('td', { class: 'num' }, v ? fmtUsd(v.judge_cost_usd) : DASH),
        h('td', { class: 'links-cell' },
          ep.has_transcript ? link(R.episode(run.run, ep.episode), 'transcript', 'small') : null,
          ep.orbit_agent ? [' ', link(R.context(run.run, { agent: ep.orbit_agent }), 'context', 'small')] : null));
    })));
}

function runRowCells(row) {
  const { run, setting, agg } = row;
  const td = (content, opts = {}) => h('td', { class: [opts.num && 'num', opts.cls], title: opts.title }, content);
  const frac = agg.judged ? agg.cheated / agg.judged : null;
  return [
    td([link(R.run(run.run), run.run, 'run-link'), run.failed ? [' ', badge('failed', 'bad', 'harness crash or provider error; not counted')] : null,
      run.mock ? [' ', badge('mock', 'plain', 'mock-model smoke test; not counted in the summary')] : null], { cls: 'wrap-cell' }),
    td([setting.label, setting.composition ? [' ', mono(setting.composition)] : null], { cls: 'wrap-cell' }),
    td(run.control || DASH),
    td(fmtInt(agg.agents), { num: true }),
    td(agg.judged ? h('span', { class: ['cheated-cell', agg.cheated && 'text-bad'] }, `${agg.cheated}/${agg.judged}`) : muted('not judged', 'small'),
      { num: true, title: frac == null ? 'no judged agent' : `${Math.round(frac * 100)}% of judged agents` }),
    td(agg.judged ? fmtInt(agg.seen) : DASH, { num: true }),
    td(agg.judged ? fmtInt(agg.attempted) : DASH, { num: true }),
    td(reportCell(agg)),
    td(registeredCell(agg)),
    td(agg.ended || DASH, { cls: 'wrap-cell small' }),
    td(agg.hasDefense ? h('span', { class: agg.blocks ? 'has-blocks' : '' }, `${fmtInt(agg.blocks)} / ${fmtInt(agg.checks)}`) : DASH, { num: true }),
    td(fmtSecs(agg.wall), { num: true }),
    td(fmtCompact(agg.prompt), { num: true, title: fmtInt(agg.prompt) }),
    td(agg.judgeCost ? fmtUsd(agg.judgeCost) : DASH, { num: true }),
  ];
}

async function viewRuns({ query: urlQ }, view) {
  const runs = await apiFresh(P.runs());
  let live = false;
  view.mounted(() => { live = true; document.title = 'Runs · results'; updateUrl(); });
  view.cleanup(() => { live = false; });
  const rows = runs.map(run => ({ run, setting: settingOf(run), agg: runAggregate(run) }));
  const settings = [...new Map(rows.map(r => [r.setting.key, r.setting])).values()].sort((a, b) => a.order - b.order || a.n - b.n);
  const controls = [...new Set(runs.map(r => r.control).filter(Boolean))].sort();
  runsStateFromQuery(urlQ, settings.map(s => s.key), controls);

  const summary = h('div', { class: 'summary-line' });
  const tbody = h('tbody');
  const ths = RUN_COLS.map(col => {
    const th = h('th', { class: [col.num && 'num', 'sortable'], title: col.title || 'click to sort', tabindex: '0', scope: 'col' }, col.label);
    const go = () => sortBy(col.key);
    th.addEventListener('click', go);
    th.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } });
    col.th = th;
    return th;
  });
  const nCols = RUN_COLS.length + 1;

  for (const row of rows) {
    const { run } = row;
    const open = openRuns.has(run.run);
    const btn = h('button', { type: 'button', class: 'expander', 'aria-expanded': open ? 'true' : 'false', 'aria-label': `agents of ${run.run}` }, open ? '▾' : '▸');
    const tr = h('tr', { class: ['run-row', run.failed && 'failed', open && 'open'], title: run.failed ? 'failed run (harness crash or provider error)' : null },
      h('td', { class: 'exp-cell' }, btn), runRowCells(row));
    const detailTd = h('td', { colspan: nCols });
    const detail = h('tr', { class: 'detail-row', hidden: !open }, detailTd);
    let built = false;
    const sync = () => {
      const isOpen = openRuns.has(run.run);
      if (isOpen && !built) { built = true; detailTd.appendChild(h('div', { class: 'detail-box' }, episodesTable(run))); }
      detail.hidden = !isOpen || tr.hidden;
      tr.classList.toggle('open', isOpen);
      btn.textContent = isOpen ? '▾' : '▸';
      btn.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
    };
    const toggle = () => { if (openRuns.has(run.run)) openRuns.delete(run.run); else openRuns.add(run.run); sync(); };
    btn.addEventListener('click', e => { e.stopPropagation(); toggle(); });
    tr.addEventListener('click', e => {
      if (e.target.closest('a, button')) return;
      const sel = window.getSelection && window.getSelection();
      if (sel && String(sel).length) return;
      toggle();
    });
    tr.dataset.run = run.run;
    row.tr = tr;
    row.detail = detail;
    row.sync = sync;
    row.hay = [run.run, run.control, row.setting.label, row.setting.composition, run.model, run.monitor_model, (run.defenses || []).join(' '),
      ...(run.episodes || []).flatMap(ep => [ep.orbit_agent, ep.failure, ep.role, ep.verdict && ep.verdict.summary])]
      .filter(Boolean).join(' \n ').toLowerCase();
    sync();
    tbody.append(tr, detail);
  }

  function band() {
    let alt = false;
    for (const row of rows) {
      if (row.tr.hidden) continue;
      alt = !alt;
      row.tr.classList.toggle('band', alt);
      row.detail.classList.toggle('band', alt);
    }
  }

  function apply() {
    const q = runsState.q.trim().toLowerCase();
    let shown = 0;
    let agents = 0;
    let judged = 0;
    let cheated = 0;
    let failedHidden = 0;
    for (const row of rows) {
      const { run, agg } = row;
      let ok = true;
      if (runsState.failed === 'hide' && run.failed) { ok = false; failedHidden++; }
      if (runsState.failed === 'only' && !run.failed) ok = false;
      if (ok && runsState.setting && row.setting.key !== runsState.setting) ok = false;
      if (ok && runsState.control && run.control !== runsState.control) ok = false;
      if (ok && runsState.cheated) {
        if (runsState.cheated === 'yes' && !(agg.cheated > 0)) ok = false;
        if (runsState.cheated === 'no' && !(agg.judged > 0 && agg.cheated === 0)) ok = false;
        if (runsState.cheated === 'none' && agg.judged > 0) ok = false;
      }
      if (ok && q && !row.hay.includes(q)) ok = false;
      row.tr.hidden = !ok;
      row.sync();
      if (ok) {
        shown++;
        agents += agg.agents;
        if (!run.failed) { judged += agg.judged; cheated += agg.cheated; }
      }
    }
    band();
    summary.replaceChildren(
      h('strong', null, plural(shown, 'run')), ` shown, ${plural(agents, 'agent')} · `,
      h('strong', { class: cheated ? 'text-bad' : '' }, `${fmtInt(cheated)} cheated`), ` of ${fmtInt(judged)} judged`,
      runsState.failed === 'hide' && failedHidden ? ` · ${plural(failedHidden, 'failed run')} hidden` : '',
      runsState.failed !== 'hide' ? ' · failed runs are never counted' : '');
    updateUrl();
  }

  function updateUrl() {
    if (!live) return;
    const target = R.runs(runsQuery());
    setHashQuietly(target);
  }

  function sortBy(key) {
    if (runsState.sortKey === key) runsState.sortDir = -runsState.sortDir;
    else { runsState.sortKey = key; runsState.sortDir = 1; }
    applySort();
  }

  function applySort() {
    const col = RUN_COLS.find(c => c.key === runsState.sortKey) || RUN_COLS[1];
    const order = rows.slice();
    order.sort((a, b) => cmpNullsLast(col.sort(a), col.sort(b), runsState.sortDir) || a.run.run.localeCompare(b.run.run, 'en', { numeric: true }));
    for (const r of order) tbody.append(r.tr, r.detail);
    for (const c of RUN_COLS) {
      c.th.classList.toggle('sorted', c === col);
      c.th.dataset.dir = c === col ? (runsState.sortDir > 0 ? 'asc' : 'desc') : '';
      c.th.setAttribute('aria-sort', c === col ? (runsState.sortDir > 0 ? 'ascending' : 'descending') : 'none');
    }
    band();
    updateUrl();
  }

  const search = h('input', {
    type: 'search', placeholder: 'Search run, control, agent, role, judge summary…', value: runsState.q,
    class: 'search', 'aria-label': 'Search runs',
  });
  search.addEventListener('input', debounce(() => { runsState.q = search.value; apply(); }, 80));
  const openAll = h('button', { type: 'button', class: 'btn btn-small', onclick: () => {
    const anyClosed = rows.some(r => !r.tr.hidden && !openRuns.has(r.run.run));
    for (const r of rows) { if (r.tr.hidden) continue; if (anyClosed) openRuns.add(r.run.run); else openRuns.delete(r.run.run); r.sync(); }
    openAll.textContent = anyClosed ? 'close all' : 'open all';
  } }, 'open all');

  const filters = h('div', { class: 'filters' },
    search,
    labeled('setting', select([['', 'all'], ...settings.map(s => [s.key, s.label])], runsState.setting, v => { runsState.setting = v; apply(); })),
    labeled('control', select([['', 'all'], ...controls], runsState.control, v => { runsState.control = v; apply(); })),
    labeled('cheated', select([['', 'any'], ['yes', 'at least one agent'], ['no', 'none of the judged'], ['none', 'not judged']], runsState.cheated,
      v => { runsState.cheated = v; apply(); })),
    labeled('failed runs', select([['hide', 'hidden'], ['show', 'shown'], ['only', 'only failed']], runsState.failed, v => { runsState.failed = v; apply(); })),
    openAll);

  const table = h('table', { class: 'data runs-table' }, h('thead', null, h('tr', null, h('th', { class: 'exp-cell', scope: 'col' }, h('span', { class: 'visually-hidden' }, 'agents')), ths)), tbody);
  const page = h('div', { class: 'page wide' },
    h('div', { class: 'page-head' },
      h('h1', null, 'Runs'),
      muted(`${plural(runs.length, 'run')}. A row opens to its agents; the run name opens the run page (metadata, board, defenses, timeline, context windows, sample).`)),
    filters, summary,
    h('div', { class: 'table-wrap runs-wrap' }, table));
  applySort();
  apply();
  return page;
}

// =====================================================================================
// 6. View: run page   #/run/<run>
// =====================================================================================

async function viewRun({ run }, view) {
  const d = await apiFresh(P.run(run));
  view.mounted(() => { document.title = `${run} · results`; });
  const rj = d.run_json || {};
  const eps = d.episodes || [];
  const logOk = !!d.eval_log_exists;
  const epFailures = [...new Set(eps.map(e => e.failure).filter(Boolean))];

  const head = h('div', { class: 'page-head' },
    h('h1', null, run),
    h('div', { class: 'chips' },
      d.failed ? badge('failed run', 'bad')
        : epFailures.length ? badge(`episode failure: ${epFailures.join(', ')}`, 'warn', 'not a harness crash, but an episode ended with a failure')
          : badge('completed', 'good'),
      badge(settingOf(d).label, 'plain', `topology in run.json: ${d.topology || '?'}`),
      badge(`control: ${d.control || 'baseline'}`, 'def')),
    h('div', { class: 'links' },
      eps.map(ep => (ep.has_transcript
        ? link(R.episode(run, ep.episode), `transcript ${ep.episode}`, 'btn')
        : h('span', { class: 'btn disabled', title: 'no transcript on disk' }, `transcript ${ep.episode}`))),
      link(R.timeline(run), 'timeline', 'btn'),
      logLink(logOk, d.eval_log, R.context(run), 'context windows', 'btn'),
      logLink(logOk, d.eval_log, R.sample(run), 'sample and workspace', 'btn')));

  const meta = section('Run metadata', kv([
    ['task', d.task],
    ['model', d.model],
    ['setting', settingOf(d).label],
    ['topology in run.json', d.topology],
    ['agents', (d.agents || []).join(', ')],
    ['control', d.control],
    ['monitor model', d.monitor_model],
    ['mode', d.mode],
    ['scale', d.scale],
    ['preset', d.preset ? (d.preset_inferred ? `${d.preset} (inferred from the config)` : d.preset) : null],
    // the access policy and the turn budget: shown only for runs that recorded them
    ['access', d.access && d.access.policy ? d.access.policy : undefined],
    ['turn budget', d.turn_budget ? `${d.turn_budget.budget} turns, ${d.turn_budget.steps_per_agent} working steps per agent` : undefined],
    ['eval id', rj.eval_id ? mono(rj.eval_id) : null],
    ['run id', rj.run_id],
    ['variant', rj.variant || null],
    ['ORBIT version', rj.orbit_version],
    ['Inspect version', rj.inspect_version],
  ], 'kv-grid'), kv([
    ['eval log', h('span', null, mono(d.eval_log), ' ', d.eval_log ? (d.eval_log_exists ? badge('present', 'good') : badge('missing', 'bad')) : null)],
    ['task root', rj.task_root ? mono(rj.task_root) : null],
    ['deviations', rj.deviations && rj.deviations.length
      ? h('ul', { class: 'plain-list' }, rj.deviations.map(x => h('li', null, mono(x)))) : null],
  ]));

  const defs = Array.isArray(rj.defenses) ? rj.defenses : [];
  const defSection = section('Defenses', defs.length ? defensesTable(defs) : muted('No defenses configured (baseline).'));

  const epSection = section(`Episodes (${eps.length})`,
    eps.length ? eps.map(ep => episodeCard(run, ep, logOk, d.eval_log)) : muted('No episodes recorded.'));

  const boardSection = section('Board messages', communicationView(d.communication, run));
  const evidence = section('Defense evidence', defenseEvidence(d.defense, defs));
  const swarmSection = rj.swarm ? section('Swarm', swarmView(rj.swarm, eps, rj.deliverables)) : null;
  const sweSection = d.swe ? section('SWE task', sweView(d.swe, eps)) : null;
  const configSection = d.config ? section('Configuration', configKv(d.config)) : null;
  const rulesSection = d.rules ? section('Turn rules', rulesTable(d.rules)) : null;
  const engagementSection = d.engagement ? section('Board engagement', engagementView(d.engagement, eps)) : null;
  const accessSection = d.access ? section('Access', accessView(d.access, eps)) : null;
  const knowledgeSection = d.knowledge ? section('Knowledge work', knowledgeView(d.knowledge)) : null;
  const toolsSection = section('Analysis tools', toolsView(run, d.tools));

  return h('div', { class: 'page' }, head, meta, configSection, defSection, sweSection, knowledgeSection, swarmSection,
    accessSection, rulesSection, engagementSection, toolsSection, epSection, boardSection, evidence);
}

// The analysis tools (tools/ at the repository root): each writes runs/<run>/tools/<tool>/summary.json and its own
// files, served by /api/runs/<run>/tools/<tool>/<file>. One card per tool present, in this order.
const TOOL_CARDS = [['scout', 'Scout', scoutCard], ['graphs', 'Network', networkCard], ['transect', 'Transect', transectCard],
  ['contagion', 'Contagion', contagionCard], ['failures', 'Failures', failuresCard], ['docent', 'Docent', docentCard],
  ['hawk', 'Hawk', hawkCard]];

/** The path inside runs/<run>/tools/<tool>/ of an absolute path a summary recorded, or null when it lies elsewhere. */
function toolRelPath(run, tool, abs) {
  if (!abs) return null;
  const marker = `${run}/tools/${tool}/`;
  const i = String(abs).lastIndexOf(marker);
  return i < 0 ? null : String(abs).slice(i + marker.length);
}

function toolLink(run, tool, file, label, attrs) {
  return h('a', { href: P.toolFile(run, tool, file), target: '_blank', rel: 'noopener', ...attrs }, label || file);
}

function toolFigure(run, tool, file, alt) {
  const src = P.toolFile(run, tool, file);
  return h('figure', { class: 'tool-fig' }, h('a', { href: src, target: '_blank', rel: 'noopener' }, h('img', { src, alt })));
}

function toolTable(cols, rows) {
  const cell = v => (v == null || v === '' ? muted(DASH) : v);
  return h('div', { class: 'table-wrap' }, h('table', { class: 'data compact tool-table' },
    h('thead', null, h('tr', null, cols.map(c => h('th', null, c)))),
    h('tbody', null, rows.map(r => h('tr', null, r.map(c => h('td', null, cell(c))))))));
}

const toolNum = (x, digits = 3) => (x == null ? null : Number.isInteger(x) ? String(x) : Number(x).toFixed(digits));
const toolPct = x => (x == null ? null : `${Math.round(x * 100)}%`);
const toolList = v => (Array.isArray(v) ? v.join(', ') : v == null ? null : String(v));

/** Every tool card present, or the one muted line that says how to make them. */
function toolsView(run, tools) {
  const names = Object.keys(tools || {});
  if (!names.length) {
    return muted(`No analysis yet: make tools-setup, then make scout RUN=${run} (and graph, transect, contagion, failures, docent, hawk).`);
  }
  const known = TOOL_CARDS.filter(([t]) => tools[t]);
  const other = names.filter(t => !TOOL_CARDS.some(([k]) => k === t)).sort();
  return h('div', { class: 'tool-cards' },
    known.map(([t, title, build]) => toolCard(run, t, title, tools[t], build)),
    other.map(t => toolCard(run, t, t, tools[t], genericToolCard)));
}

function toolCard(run, tool, title, s, build) {
  let body;
  if (s && s.error && !s.tool) body = h('p', { class: 'tool-line' }, badge('summary unreadable', 'bad'), ' ', muted(String(s.error)));
  else {
    try {
      body = build(run, s, tool);
    } catch (e) {
      body = badge('could not draw this summary', 'bad', String((e && e.message) || e));
    }
  }
  return h('div', { class: 'tool-card' },
    h('div', { class: 'tool-head' }, h('h3', null, title),
      muted(`written ${fmtTime(s && s.written_at)}`, 'small'), toolLink(run, tool, 'summary.json', 'summary.json', { class: 'small' })),
    body);
}

function genericToolCard(run, s) {
  const scalars = Object.entries(s).filter(([k, v]) => !['tool', 'run', 'written_at'].includes(k) && (v == null || typeof v !== 'object'));
  return h('div', null, kv(scalars.map(([k, v]) => [k, String(v)]), 'kv-grid'),
    lazyDetails('summary.json', () => longText(JSON.stringify(s, null, 2), { lines: 40 }), { cls: 'sub' }));
}

// Scout: one row per agent, one column per scanner. A found signal shows the model's message number (M) and the
// timeline round; its explanation and quote are on hover and in the explanations below.
const SCOUT_KIND = { honeypot_seen: 'warn', regulator_intervened: 'good' };

function scoutCard(run, s) {
  const signals = s.scanners || [];
  const agents = Object.keys(s.agents || {}).sort();
  const val = s.validation || {};
  const judged = val.agents || {};
  const tipOf = f => [f.explanation, f.quote ? `quote: ${f.quote}` : null].filter(Boolean).join('\n\n');
  const cell = (sig, f) => {
    if (!f) return null;
    if (f.error) return badge('error', 'warn', String(f.error));
    if (!f.found) return h('span', { title: tipOf(f) }, badge('no', 'plain'));
    const where = [f.step != null ? `M${f.step}` : null, f.round != null ? `round ${f.round}` : null].filter(Boolean).join(' · ');
    return h('span', { title: tipOf(f) }, badge('yes', SCOUT_KIND[sig] || 'bad'), where ? [' ', muted(where, 'small')] : null);
  };
  const cols = ['agent', 'judge: cheated', 'onset round', ...signals.map(sig => sig.replace(/^honeypot_/, 'honeypot ').replace(/_/g, ' '))];
  const rows = agents.map(a => {
    const j = (judged[a] || {}).judge || {};
    const onset = (s.onset || {})[a];
    return [a, yn(j.cheated, 'bad', 'good'), onset == null ? null : String(onset), ...signals.map(sig => cell(sig, s.agents[a][sig]))];
  });
  const agreement = Object.entries(val.agreement || {});
  const agreeLine = agreement.length ? h('p', { class: 'tool-line' }, 'Agreement with the judge: ',
    agreement.map(([k, v]) => [badge(`${k.replace('~', ' ~ ')} ${v.agree}/${v.agree + v.disagree + v.unknown}`,
      v.disagree ? 'warn' : 'good', `${v.agree} agree, ${v.disagree} disagree, ${v.unknown} unknown`), ' '])) : null;
  const explanations = () => h('div', null, agents.map(a => h('div', null, h('h4', { class: 'sub-h' }, a),
    kv(signals.map(sig => {
      const f = s.agents[a][sig] || {};
      const head = f.error ? `error: ${f.error}` : f.found ? `found (M${f.step ?? '?'}, round ${f.round ?? '?'})` : 'not found';
      return [sig.replace(/_/g, ' '), h('div', null, h('strong', null, head), f.explanation ? h('p', { class: 'tool-expl' }, f.explanation) : null,
        f.quote ? h('pre', { class: 'pre tool-quote' }, f.quote) : null)];
    })))));
  const files = s.files || [];
  const scan = s.scan || {};
  return h('div', null,
    kv([['model', s.model || 'grep only'], ['scan complete', scan.complete == null ? null : yn(scan.complete, 'good', 'warn')],
      ['scan errors', scan.errors ? String(scan.errors.length) : null],
      ['files', files.length ? h('span', null, files.map(f => [toolLink(run, 'scout', f), ' '])) : null]], 'kv-grid'),
    toolTable(cols, rows),
    agreeLine,
    lazyDetails('explanations and quotes', explanations, { cls: 'sub' }),
    files.includes('onset.png') ? lazyDetails('onset figure', () => toolFigure(run, 'scout', 'onset.png', 'Scout onset rounds'), { cls: 'sub' }) : null);
}

// Network (tools/graphs): who read whom on the board, and, with scout's onsets, who started and how it spread.
function networkCard(run, s) {
  const files = s.files || [];
  const degree = s.degree || {};
  const check = s.read_graph_check;
  const nodes = (s.nodes || []).map(n => [n.agent, n.role, yn(n.cheated, 'bad', 'good'), n.onset == null ? null : String(n.onset),
    n.discovery_round == null ? null : String(n.discovery_round), String(n.posts ?? 0), String(n.reads ?? 0),
    String((degree[n.agent] || {}).in ?? 0), String((degree[n.agent] || {}).out ?? 0)]);
  const edges = (s.edges || []).map(e => [`${e.source} → ${e.target}`, e.kind, String(e.weight), e.first_round == null ? null : String(e.first_round),
    e.distinct_posts == null ? null : String(e.distinct_posts)]);
  const exposure = Object.entries(s.exposure_before_onset || {}).flatMap(([a, list]) => (list || []).map(x =>
    [a, x.sender, `#${x.number}`, x.round == null ? null : String(x.round), x.read_round == null ? null : String(x.read_round),
      yn(x.exposed_to_cheater, 'bad', 'plain')]));
  const spread = (s.spread_order || []).map(x => [x.agent, String(x.round ?? DASH), x.step == null ? null : String(x.step),
    x.turn == null ? null : String(x.turn)]);
  const contagion = (s.contagion_edges || []).map(e => [`${e.source} → ${e.target}`, toolList(e.posts)]);
  const onsetParts = s.onset_source
    ? [h('h4', { class: 'sub-h' }, 'Spread'), kv([['who started', s.who_started]]),
      spread.length ? toolTable(['agent', 'onset round', 'step', 'turn'], spread) : muted('No agent has an onset.'),
      h('h4', { class: 'sub-h' }, 'Contagion edges'),
      contagion.length ? toolTable(['edge', 'posts read before onset'], contagion) : muted('No agent read a post before its own onset.'),
      h('h4', { class: 'sub-h' }, 'Exposure before onset'),
      exposure.length ? toolTable(['agent', 'sender', 'post', 'post round', 'read round', 'sender cheated'], exposure)
        : muted('No posts read before an onset.')]
    : [muted('No onsets in this summary, so who started, the spread order, contagion edges and exposure are empty. Run scout, then graph.')];
  return h('div', null,
    files.includes('graph.png') ? toolFigure(run, 'graphs', 'graph.png', 'Board read graph') : null,
    kv([['nodes', String(s.n_nodes ?? DASH)], ['edges', String(s.n_edges ?? DASH)], ['density', toolNum(s.density)], ['layout', s.layout],
      ['onset source', s.onset_source || 'none'],
      ['read graph matches run.json', check ? yn(check.matches, 'good', 'warn') : null],
      ['files', h('span', null, files.includes('graph.gexf') ? [toolLink(run, 'graphs', 'graph.gexf', 'graph.gexf (open in Gephi)', { download: 'graph.gexf' }), ' '] : null,
        files.filter(f => f !== 'graph.gexf' && f !== 'graph.png').map(f => [toolLink(run, 'graphs', f), ' ']))]], 'kv-grid'),
    toolTable(['agent', 'role', 'cheated', 'onset', 'discovery round', 'posts', 'reads', 'in', 'out'], nodes),
    edges.length ? toolTable(['edge', 'kind', 'weight', 'first round', 'distinct posts'], edges) : muted('No edges.'),
    onsetParts);
}

// Transect: one report per agent and the phases its judge labelled. Turns are Transect's (0-based); rounds come from the timeline.
function transectCard(run, s) {
  const reports = s.reports || {};
  const agents = s.agents || {};
  const reportLinks = Object.entries(reports).filter(([, p]) => p).map(([k, p]) => {
    const rel = toolRelPath(run, 'transect', p);
    const label = k === 'run' ? 'merged sample report (structural)' : `${k} report`;
    return rel ? [toolLink(run, 'transect', rel, label, { class: 'btn btn-small' }), ' '] : [mono(p), ' '];
  });
  const span = (a, b) => (a == null ? null : a === b ? String(a) : `${a}–${b}`);
  const phaseRows = Object.entries(s.phases || {}).sort(([a], [b]) => a.localeCompare(b)).flatMap(([a, list]) =>
    (list || []).map((p, i) => [i === 0 ? a : h('span'), i === 0 ? yn((agents[a] || {}).cheated, 'bad', 'good') : h('span'),
      badge(p.label, /^exploit/.test(p.label) ? 'bad' : 'plain'), span(p.start_turn, p.end_turn), span(p.start_round, p.end_round)]));
  const judge = s.judge;
  const merged = s.merged_sample || {};
  return h('div', null,
    kv([['judge', judge ? judge.model : 'structural only (no model calls)'],
      ['rolls', judge ? String(judge.k_rolls) : undefined], ['verify', judge ? yn(judge.verify, 'plain', 'plain') : undefined],
      ['judge tokens', judge && judge.totals ? fmtInt(judge.totals.total_tokens) : undefined],
      ['merged sample accepted', merged.sample ? h('span', { title: (merged.errors || []).join('\n') }, yn(merged.accepted, 'good', 'warn')) : undefined]], 'kv-grid'),
    h('div', { class: 'links' }, reportLinks),
    phaseRows.length ? toolTable(['agent', 'cheated', 'phase', 'turns', 'rounds'], phaseRows)
      : muted(s.structural_only ? 'Structural run: no phases were labelled.' : 'No phases.'));
}

// Contagion: the observed shares and the SI, threshold and SIR fits, or why the fit was skipped.
function contagionCard(run, s) {
  const files = s.files || [];
  const obs = s.observed_final || {};
  const fitRows = Object.entries(s.fits || {}).flatMap(([behaviour, fits]) => (fits && fits.skipped
    ? [[behaviour, muted('skipped'), fits.skipped.join('; '), null, null]]
    : Object.entries(fits || {}).map(([model, f]) => [behaviour, model,
      Object.entries(f.parameters || {}).map(([k, v]) => `${k} ${toolNum(v, 4)}`).join(', '), toolPct(f.final_size), toolNum(f.ssr, 4)])));
  const allSkipped = Object.values(s.fits || {}).every(fits => fits && fits.skipped);
  return h('div', null,
    files.includes('contagion.png') ? toolFigure(run, 'contagion', 'contagion.png', 'Contagion curves and fits') : null,
    kv([['graph source', s.graph_source], ['onset source', s.onset_source], ['agents', String(s.n_agents ?? DASH)],
      ['regulators', String(s.n_regulators ?? DASH)], ['rounds', String(s.rounds ?? DASH)], ['simulations', String(s.simulations ?? DASH)],
      ['seed', String(s.seed ?? DASH)], ['dishonest at the end', toolPct(obs.dishonest)], ['honest at the end', toolPct(obs.honest)]], 'kv-grid'),
    s.degenerate ? h('p', { class: 'tool-line' }, badge('degenerate', 'warn', 'the fits are not meaningful on this run'), ' ', muted(s.reason || '')) : null,
    // when every fit was skipped, the degenerate note above already gives the reasons
    allSkipped && s.degenerate ? null
      : fitRows.length ? toolTable(['behaviour', 'model', 'parameters', 'final size', 'SSR'], fitRows) : muted('No fits in this summary.'),
    (s.notes || []).length ? h('ul', { class: 'plain-list' }, s.notes.map(n => h('li', null, n))) : null,
    h('div', { class: 'links' }, files.filter(f => f !== 'contagion.png').map(f => [toolLink(run, 'contagion', f), ' '])));
}

// Failures: MAST's modes marked yes and Who&When's decisive agent and step by three methods. The mode names and the
// reasons are in mast.json and who_when.json, fetched when the card is drawn.
const WHO_WHEN_METHODS = [['all_at_once', 'all at once'], ['step_by_step', 'step by step'], ['binary_search', 'binary search']];

function failuresCard(run, s) {
  const holder = h('div');
  const draw = (mast, whoWhen) => {
    const m = s.mast;
    // a mode's name as the reply gave it (gpt-6-luna swaps 3.2 and 3.3 between replies), else the taxonomy's
    const names = (mast && mast.names) || {};
    const replyNames = (mast && mast.reply_names) || {};
    const nameOf = k => replyNames[k] || names[k];
    const yes = m ? (m.yes_strict || m.yes || []) : [];
    const mastPart = !m ? muted('MAST was not run.') : h('div', null,
      kv([['task completed', m.task_completed == null ? null : yn(m.task_completed === 'yes', 'good', 'warn')],
        ['modes marked yes', yes.length ? h('span', null, yes.map(k => [badge(nameOf(k) ? `${k} ${nameOf(k)}` : k, 'bad'), ' '])) : muted('none')],
        ['parser disagreements', (m.disagreements || []).length ? badge(m.disagreements.join(', '), 'warn', 'the notebook parser and the strict parser differ') : undefined]]),
      m.summary ? h('p', { class: 'verdict-summary' }, m.summary) : null);
    const ww = s.who_when;
    const detail = whoWhen || {};
    const rows = !ww ? [] : WHO_WHEN_METHODS.filter(([k]) => ww[k]).map(([k, label]) => {
      const r = ww[k];
      const d = detail[k] || {};
      const stepAgent = d.step_agent && d.step_agent !== r.agent ? muted(` (step ${r.step} belongs to ${d.step_agent})`, 'small') : null;
      return [label, h('span', null, r.agent || DASH, stepAgent), r.step == null ? null : String(r.step), r.round == null ? null : String(r.round),
        r.kind, d.reason ? h('span', { class: 'tool-expl' }, d.reason) : null];
    });
    holder.textContent = '';
    appendAll(holder, [h('h4', { class: 'sub-h' }, `MAST (${s.model || '?'})`), mastPart,
      h('h4', { class: 'sub-h' }, 'Who&When'), ww ? toolTable(['method', 'agent', 'step', 'round', 'kind', 'reason'], rows) : muted('Who&When was not run.'),
      h('div', { class: 'links' }, s.mast ? [toolLink(run, 'failures', 'mast.json'), ' '] : null,
        ww ? toolLink(run, 'failures', 'who_when.json') : null)]);
  };
  // the detail files exist only for the parts that ran; fetching a missing one would log a 404
  const detailOf = (part, file) => (s[part] ? fetchJson(P.toolFile(run, 'failures', file)).catch(() => null) : Promise.resolve(null));
  draw(null, null);
  Promise.all([detailOf('mast', 'mast.json'), detailOf('who_when', 'who_when.json')]).then(([mast, whoWhen]) => {
    try {
      draw(mast, whoWhen);
    } catch (e) {
      appendAll(holder, [badge('could not draw the failures details', 'bad', String((e && e.message) || e))]);
    }
  });
  return holder;
}

// Docent: the collection after an upload, or what a dry run prepared.
function docentCard(run, s) {
  const runs = (s.agent_runs || []).map(r => [r.name, r.kind, r.kind === 'agent' ? yn(r.cheated, 'bad', 'good') : null, String(r.messages ?? DASH)]);
  const head = s.collection_url
    ? kv([['collection', h('a', { href: s.collection_url, target: '_blank', rel: 'noopener' }, s.collection_name || s.collection_id || s.collection_url)],
      ['collection id', s.collection_id ? mono(s.collection_id) : null], ['upload status', s.upload_status ? String(asText(s.upload_status)) : null],
      ['agent runs', String(s.n_agent_runs ?? runs.length)]], 'kv-grid')
    : h('p', { class: 'tool-line' }, badge(s.dry_run ? 'dry run' : 'not uploaded', 'warn'), ' ',
      `${plural(s.n_agent_runs ?? runs.length, 'AgentRun')} prepared${s.board ? ' (with the board)' : ''}, not uploaded. `,
      s.agent_runs_file ? toolLink(run, 'docent', s.agent_runs_file) : null);
  return h('div', null, head, runs.length ? toolTable(['agent run', 'kind', 'cheated', 'messages'], runs) : null);
}

// Hawk: the scan config and whether the scan ran.
function hawkCard(run, s) {
  const rel = toolRelPath(run, 'hawk', s.config);
  const ran = s.ran ? badge(s.returncode === 0 ? 'ran' : `ran, exit ${s.returncode}`, s.returncode === 0 ? 'good' : 'bad')
    : badge('config only, not run', 'plain');
  return h('div', null,
    kv([['config', rel ? toolLink(run, 'hawk', rel, s.config) : mono(s.config)], ['scan', ran],
      ['model', s.model], ['source', s.source ? `${s.source.kind}: ${s.source.path}` : null],
      ['scanners', toolList(s.scanners)], ['results', (s.results || []).length ? h('span', null, s.results.map(r => [mono(r), ' '])) : null]]),
    s.command ? lazyDetails('command', () => longText(s.command, { lines: 6 }), { cls: 'sub' }) : null,
    (s.stdout_tail || []).length ? lazyDetails('output (last lines)', () => longText(s.stdout_tail.join('\n'), { lines: 40 }), { cls: 'sub' }) : null,
    (s.notes || []).length ? lazyDetails('notes', () => h('ul', { class: 'plain-list' }, s.notes.map(n => h('li', null, n))), { cls: 'sub' }) : null);
}

// The access policy and what each agent held (run.json "access"): its items, and on openmath its honeypot branches
// and archive slices, on knowledge work the review tool and the read permission on the pipeline artifact.
function accessView(access, eps) {
  const agents = Object.keys(access.agents || {}).sort();
  const roleOf = a => (eps.find(e => e.orbit_agent === a) || {}).role;
  const branchy = agents.some(a => (access.agents[a] || {}).branches !== undefined);
  const sliced = agents.some(a => 'slices' in (access.agents[a] || {}));
  const kw = agents.some(a => 'review' in (access.agents[a] || {}));
  const archive = access.archive ? access.archive.name : 'archive';
  const cols = ['agent', 'role', 'holds', ...(branchy ? ['honeypot branches held'] : []),
    ...(sliced ? [archive + ' held'] : []), ...(kw ? ['review tool', 'artifact read'] : [])];
  const table = h('div', { class: 'table-wrap' }, h('table', { class: 'data' },
    h('thead', null, h('tr', null, cols.map(c => h('th', null, c)))),
    h('tbody', null, agents.map(a => {
      const x = access.agents[a] || {};
      const cells = [h('td', null, x.holds == null ? muted(DASH) : (x.holds.length ? mono(x.holds.join(' + ')) : muted('nothing')))];
      if (branchy) cells.push(h('td', null, (x.branches || []).length ? mono(x.branches.join(', ')) : muted('none')));
      if (sliced) {
        cells.push(h('td', null, x.slices == null ? 'whole' : (x.slices.length
          ? mono(x.slices.map(k => `${archive}.part${k}`).join(', ') + ` of ${access.archive ? access.archive.parts : '?'}`)
          : muted('none'))));
      }
      if (kw) cells.push(h('td', null, yn(x.review, 'plain', 'plain')), h('td', null, yn(x.read, 'plain', 'plain')));
      return h('tr', null, h('td', null, a), h('td', null, roleOf(a) || muted(DASH)), ...cells);
    }))));
  return h('div', null, kv([
    ['policy', access.policy || undefined],
    ['team remote', access.remote ? mono(access.remote) : undefined],
    ['honeypot branches', access.honeypot_branches ? mono(access.honeypot_branches.join(', ')) : undefined],
    ['split archive', access.archive && access.archive.parts
      ? mono(`${access.archive.name} on ${access.archive.branch}, ${access.archive.parts} slices`) : undefined],
  ], 'kv-grid'), table);
}

// The knowledge-work sample and its reviews (run.json "knowledge"): the layout, the pipeline artifact, each contract's
// to_send/ files and each agent's grader state.
function knowledgeView(kw) {
  const meta = kw.meta || {};
  const state = kw.grader_state || {};
  const last = kw.last_verdict || null;
  const contracts = Object.entries(kw.contracts || {});
  const agents = Object.keys(state).sort();
  const reviewTable = h('div', { class: 'table-wrap' }, h('table', { class: 'data' },
    h('thead', null, h('tr', null, ['agent', 'candidate', 'reviews', 'errors', 'verdicts', 'submitted the reference']
      .map(c => h('th', null, c)))),
    h('tbody', null, agents.map(a => {
      const g = state[a] || {};
      return h('tr', null, h('td', null, a), h('td', null, mono(g.agent_id)), h('td', null, String(g.calls ?? DASH)),
        h('td', null, String(g.errors ?? DASH)), h('td', null, (g.verdicts || []).join(', ') || muted(DASH)),
        h('td', null, String(g.submitted_reference ?? DASH)));
    }))));
  const contractTable = h('div', { class: 'table-wrap' }, h('table', { class: 'data' },
    h('thead', null, h('tr', null, ['contract', 'candidate', 'files in to_send/', 'copied the artifact']
      .map(c => h('th', null, c)))),
    h('tbody', null, contracts.map(([c, x]) => h('tr', null, h('td', null, mono(c)), h('td', null, mono(x.candidate)),
      h('td', null, (x.files || []).length ? (x.files || []).map(f => h('div', null, mono(`${f.path} (${f.bytes} B)`))) : muted('none')),
      h('td', null, yn(x.copied_reference, 'bad', 'plain')))))));
  return h('div', null, kv([
    ['row', mono(kw.row_id)],
    ['occupation', meta.occupation || null],
    ['layout', kw.layout],
    ['mode', kw.mode],
    ['pipeline artifact', mono(kw.comparator)],
    ['share of contracts delivered', kw.share_delivered == null ? null : String(kw.share_delivered)],
    ['reviews', kw.reviews == null ? null : String(kw.reviews)],
    ['last verdict', last ? `${last.verdict || last.error_type || DASH} (${last.agent}, ${last.deliverable})` : null],
  ], 'kv-grid'), h('h3', null, 'Reviews per agent'), reviewTable, h('h3', null, 'Contracts'), contractTable);
}

// Who looked at the board, read it, posted and submitted for the team: run.json "engagement" and each episode's.
function engagementView(run, eps) {
  const sub = run.team_submission;
  const summary = kv([
    ['agents on the board', run.agents],
    ['agents that looked', run.agents_checked],
    ['agents that posted', run.agents_posted],
    ['median discovery round', run.median_discovery_round],
    ['checks', run.checks],
    ['reads', run.reads],
    ['posts', run.posts],
    ['team submission', sub ? `${sub.agent} in round ${sub.round == null ? DASH : sub.round}` : null],
    ['reads refused by the budget', run.reads_refused],
    ['posts refused by the budget', run.posts_refused],
    ['oversight queries (agent_actions)', run.oversight],
  ], 'kv-grid');
  const rows = eps.filter(e => e.engagement);
  const cell = v => (v == null ? muted(DASH) : String(v));
  const senders = m => Object.entries(m || {}).sort().map(([s, n]) => `${s} ${n}`).join(', ');
  // per-turn budgets and a regulator's agent_actions: columns only for runs that recorded them
  const newer = rows.some(e => 'reads_refused' in e.engagement);
  const cols = ['agent', 'role', 'discovery round', 'checks', 'reads', 'posts', 'work steps', 'free steps',
    'notices', 'team submit', 'read from',
    ...(newer ? ['reads refused', 'posts refused', 'oversight queries', 'queried'] : [])];
  const table = h('div', { class: 'table-wrap' }, h('table', { class: 'data' },
    h('thead', null, h('tr', null, cols.map(c => h('th', null, c)))),
    h('tbody', null, rows.map(e => {
      const g = e.engagement;
      return h('tr', null, h('td', null, e.orbit_agent), h('td', null, cell(e.role)), h('td', null, cell(g.discovery_round)),
        h('td', null, cell(g.checks)), h('td', null, cell(g.reads)), h('td', null, cell(g.posts)),
        h('td', null, cell(g.work_steps)), h('td', null, cell(g.free_steps)), h('td', null, cell(g.notices)),
        h('td', null, g.team_submitted ? 'yes' : 'no'), h('td', null, senders(g.read_senders) || muted(DASH)),
        ...(newer ? [h('td', null, cell(g.reads_refused)), h('td', null, cell(g.posts_refused)),
          h('td', null, cell(g.oversight)), h('td', null, senders(g.oversight_targets) || muted(DASH))] : []));
    }))));
  return h('div', null, summary, table);
}

// The run's SwarmConfig as the task built it (run.json "config"), one field per row.
function configKv(config) {
  const value = v => (typeof v === 'boolean' ? yn(v, 'plain', 'plain') : typeof v === 'object' && v !== null ? mono(asText(v)) : mono(String(v)));
  return kv(Object.entries(config).map(([k, v]) => [k.replace(/_/g, ' '), v == null ? null : value(v)]), 'kv-grid');
}

// What the turn rules recorded for each agent (episode "rules"): one row per agent, one column per fact.
function rulesTable(rules) {
  const agents = Object.keys(rules).sort();
  const facts = [...new Set(agents.flatMap(a => Object.keys(rules[a] || {})))];
  const cell = v => (v == null ? muted(DASH) : typeof v === 'object' ? mono(JSON.stringify(v)) : String(v));
  return h('div', { class: 'table-wrap' }, h('table', { class: 'data' },
    h('thead', null, h('tr', null, h('th', null, 'agent'), facts.map(f => h('th', null, f.replace(/_/g, ' '))))),
    h('tbody', null, agents.map(a => h('tr', null, h('td', null, a), facts.map(f => h('td', null, cell((rules[a] || {})[f]))))))));
}

// A CheatBench SWE task: the arm, each agent's jigsaw fragment or planted piece, the
// hidden-test rewards for the team's main and each agent's working tree, the judge, the rung and the talk measures.
function sweView(swe, eps) {
  const epOf = name => eps.find(e => e.orbit_agent === name) || null;
  const agents = eps.map(e => e.orbit_agent).filter(Boolean).sort();
  const reward = k => (swe.rewards || {})[k] == null ? muted(DASH)
    : h('span', { title: `${(swe.required_passed || {})[k] || 0} of ${swe.required_tests} required tests passed` },
      yn(swe.rewards[k] === 1, 'good', 'plain'));
  const m = (a, k) => ((swe.measures || {})[a] || {})[k];
  const cols = ['agent', swe.jigsaw ? 'fragment files' : null, swe.pieces ? 'planted piece' : null, 'reward',
    'judge: cheated', 'rung', 'posts', 'about the fix', 'disclosed'].filter(Boolean);
  const table = h('table', { class: 'data' },
    h('thead', null, h('tr', null, cols.map(c => h('th', null, c)))),
    h('tbody', null, agents.map(a => h('tr', null,
      h('td', null, a),
      swe.jigsaw ? h('td', null, ((swe.fragments || {})[a] || []).map(f => h('div', null, mono(f)))) : null,
      swe.pieces ? h('td', null, (swe.pieces || {})[a] == null ? muted(DASH) : mono(asText(swe.pieces[a]))) : null,
      h('td', null, reward(a)),
      h('td', null, verdictCell(epOf(a))),
      h('td', null, ((swe.rungs || {})[a]) || muted(DASH)),
      h('td', null, m(a, 'posts') == null ? muted(DASH) : String(m(a, 'posts'))),
      h('td', null, m(a, 'fix_posts') == null ? muted(DASH) : `${m(a, 'fix_posts')} (rule ${m(a, 'rule_posts')})`),
      h('td', null, m(a, 'disclosed') == null ? muted(DASH) : yn(m(a, 'disclosed'), 'plain', 'warn'))))));
  return h('div', null,
    kv([
      ['task', h('span', null, mono(swe.task_key || DASH), ' ', muted(swe.instance || '', 'small'))],
      ['arm', mono(swe.arm || DASH)],
      ['agents', String(swe.n_agents)],
      ['honeypot', swe.honeypot ? mono(swe.honeypot) : muted('as released')],
      ['rule', swe.rule === 'none' ? muted('none (standard prompt)') : h('span', null, mono(swe.rule), ' ', muted(swe.rule_sentence || '', 'small'))],
      ['board delivery', swe.delivery || muted(DASH)],
      ['opening turns', String(swe.comm_turns || 0)],
      ['team reward (team/main)', swe.board ? reward('team') : muted('lone agent: its working tree ships')],
      ['jigsaw isolation check', swe.jigsaw ? yn(swe.jigsaw_isolation_ok, 'good', 'bad') : muted(DASH)],
      ['base / fix commit', h('span', null, mono((swe.base_commit || '').slice(0, 10)), ' / ', mono((swe.fix_commit || '').slice(0, 10)))],
    ], 'kv-grid'),
    h('h3', null, 'Agents'), h('div', { class: 'table-wrap' }, table));
}

// Composition, hidden roles and the team's own report of a swarm run, beside the judge's verdicts,
// plus what the team registered (run.json deliverables, from the registry on main) and how each agent ended.
function swarmView(sw, eps, deliverables) {
  const del = deliverables || null;
  const endings = {};
  eps.forEach(e => { const k = e.failure || (e.ended_by === 'submit' ? 'submit' : (e.ended_by || 'unknown')); endings[k] = (endings[k] || 0) + 1; });
  const endedText = Object.entries(endings).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([k, n]) => `${n} ${k}`).join(', ');
  const epOf = name => eps.find(e => e.orbit_agent === name) || null;
  const roles = Object.entries(sw.roles || {}).sort(([a], [b]) => a.localeCompare(b));
  // Counted verdicts follow /api/summary: judged episodes that have a transcript.
  const counted = eps.filter(e => e.verdict && e.verdict.cheated != null && e.has_transcript);
  const blind = eps.filter(e => e.verdict && e.verdict.cheated != null && !e.has_transcript).length;
  const anyCheated = counted.some(e => e.verdict.cheated === true);
  const judged = counted.length;
  const table = h('table', { class: 'data' },
    h('thead', null, h('tr', null, ['agent', 'hidden role', 'judge: cheated'].map(c => h('th', null, c)))),
    h('tbody', null, roles.map(([name, role]) => h('tr', null,
      h('td', null, name), h('td', null, roleBadge(role)), h('td', null, verdictCell(epOf(name)))))));
  const reportVerdict = sw.swarm_report_cheated;
  const agree = reportVerdict == null || !judged ? null : (reportVerdict === anyCheated);
  return h('div', null,
    kv([
      ['composition', h('span', null, mono(sw.composition || DASH), ' ', muted(`${sw.regulators} regulators, ${sw.disregulators} disregulators, ${sw.workers} workers; seed ${sw.seed}`, 'small'))],
      ['judge: any agent cheated', judged ? yn(anyCheated, 'bad', 'good')
        : muted(blind ? `not counted: ${plural(blind, 'verdict')} on episodes without a transcript` : 'not judged')],
      ['team report says cheated', sw.swarm_report == null ? badge('no report file', 'warn') : yn(reportVerdict, 'bad', 'good')],
      ['report agrees with judge', agree == null ? muted(DASH) : yn(agree, 'plain', 'warn')],
      ['registered on main', del == null ? muted('not recorded') : h('span', { title: `checkout ${del.checkout || DASH}; registry on main: ${(del.main_registry_agents || []).join(', ')}` }, yn(del.registered_on_main, 'good', 'warn'))],
      ['archive in workspace at end', del == null ? muted('not recorded') : yn(del.archive_in_workspace, 'plain', 'warn')],
      ['agents ended by', eps.length ? endedText : muted(DASH)],
    ], 'kv-grid'),
    del && del.commits && del.commits.length ? h('div', null, h('h3', null, 'Commits at the end (git log --all)'), h('pre', { class: 'pre' }, del.commits.join('\n'))) : null,
    sw.swarm_report ? h('div', null, h('h3', null, 'swarm_report.txt'), h('pre', { class: 'pre' }, String(sw.swarm_report))) : null,
    h('h3', null, 'Roles'), table);
}

function defensesTable(defs) {
  const cols = ['name', 'defense_type', 'defense_category', 'invocation', 'model_override', 'scope', 'fail_mode',
    'detection_threshold', 'max_resamples', 'enabled'];
  const table = h('table', { class: 'data' },
    h('thead', null, h('tr', null, cols.map(c => h('th', null, c.replace(/_/g, ' '))), h('th', null, 'config keys'))),
    h('tbody', null, defs.map(df => {
      const d = typeof df === 'object' && df ? df : { name: String(df) };
      const cfgKeys = d.config && typeof d.config === 'object' ? Object.keys(d.config) : [];
      return h('tr', null,
        cols.map(c => h('td', { class: c === 'name' || c === 'defense_type' ? 'mono' : '' },
          d[c] == null || d[c] === '' ? muted(DASH) : asText(d[c]))),
        h('td', null, cfgKeys.length ? cfgKeys.map(k => badge(k, 'plain')) : muted('none')));
    })));
  const configs = defs.filter(d => d && typeof d === 'object').map(d => {
    const extra = {};
    for (const k of Object.keys(d).filter(k => k !== 'name' && !cols.includes(k))) {
      if (d[k] != null && !(Array.isArray(d[k]) && !d[k].length) && d[k] !== '' &&
          !(typeof d[k] === 'object' && !Array.isArray(d[k]) && !Object.keys(d[k]).length)) extra[k] = d[k];
    }
    if (!Object.keys(extra).length) return null;
    return lazyDetails(`${d.name} configuration`, () => configView(extra), { cls: 'sub' });
  });
  return [h('div', { class: 'table-wrap' }, table), configs];
}

/** Show a config object: string values as text blocks, others as JSON. */
function configView(obj) {
  const box = h('div', { class: 'config' });
  const walk = (o, prefix) => {
    for (const [k, v] of Object.entries(o)) {
      const key = prefix ? `${prefix}.${k}` : k;
      if (v && typeof v === 'object' && !Array.isArray(v) && Object.values(v).some(x => typeof x === 'string' && x.length > 60)) {
        walk(v, key);
      } else {
        box.appendChild(h('div', { class: 'cfg-key mono' }, key));
        box.appendChild(longText(typeof v === 'string' ? v : JSON.stringify(v, null, 2), { lines: 40 }));
      }
    }
  };
  walk(obj, '');
  return box;
}

function episodeCard(run, ep, logOk = true, evalLog = null) {
  return h('div', { class: 'ep-card' },
    h('div', { class: 'ep-card-head' },
      h('h3', null, ep.episode),
      ep.failure ? badge(ep.failure, 'bad') : null,
      h('span', { class: 'spacer' }),
      ep.has_transcript ? link(R.episode(run, ep.episode), 'transcript', 'btn btn-small') : muted('no transcript', 'small'),
      ep.orbit_agent ? logLink(logOk, evalLog, R.context(run, { agent: ep.orbit_agent }), `context of ${ep.orbit_agent}`, 'btn btn-small') : null),
    episodeRecord(ep),
    verdictBlock(ep.verdict),
    ladderBlock(ep.ladder),
    signalsBlock(ep.signals),
    ep.error ? lazyDetails('error', () => longText(ep.error, { lines: 30 }), { cls: 'sub' }) : null,
    ep.final_text ? lazyDetails('final text', () => longText(ep.final_text, { lines: 40 }), { cls: 'sub' }) : null);
}

function communicationView(comm, run) {
  const samples = Object.entries(comm || {});
  if (!samples.length) return muted('No board communication recorded for this run.');
  return samples.map(([sample, c]) => {
    const msgs = ((c && c.messages) || []).slice().sort((a, b) => (a.sequence ?? 0) - (b.sequence ?? 0));
    const events = (c && c.events) || [];
    const kinds = {};
    for (const e of events) kinds[e.kind || '?'] = (kinds[e.kind || '?'] || 0) + 1;
    return h('div', { class: 'board' },
      h('div', { class: 'board-head' },
        h('h3', null, `sample ${sample}`),
        muted(`${plural(msgs.length, 'message')} · ${plural(events.length, 'event')}` +
          (events.length ? ` (${Object.entries(kinds).map(([k, n]) => `${k} ${n}`).join(', ')})` : ''))),
      msgs.length ? h('ol', { class: 'board-msgs' }, msgs.map(m => h('li', { class: 'board-msg' },
        h('div', { class: 'bm-head' },
          h('span', { class: 'bm-seq' }, `#${m.sequence ?? '?'}`),
          h('span', { class: 'bm-sender' }, m.sender || '?'),
          h('span', { class: 'muted' }, ' → ', (m.recipients || []).join(', ') || 'all'),
          m.turn != null ? muted(` · turn ${m.turn}`) : null,
          m.channel_id ? muted(` · ${m.channel_id}`) : null,
          h('span', { class: 'spacer' }),
          m.id ? h('span', { class: 'tc-id' }, m.id) : null),
        longText(m.content, { lines: 25, cls: 'msg-text' }))))
        : muted('No messages were posted.'),
      events.length ? lazyDetails(`board events (${events.length})`, () => h('div', { class: 'table-wrap' },
        h('table', { class: 'data compact' },
          h('thead', null, h('tr', null, ['seq', 'kind', 'agent', 'message ids', 'tool call', 'channel'].map(x => h('th', null, x)))),
          h('tbody', null, events.slice().sort((a, b) => (a.sequence ?? 0) - (b.sequence ?? 0)).map(e => h('tr', null,
            h('td', { class: 'num' }, String(e.sequence ?? '')),
            h('td', null, e.kind || ''),
            h('td', null, e.agent_id || ''),
            h('td', { class: 'mono small' }, (e.message_ids || []).join(', ') || DASH),
            h('td', { class: 'mono small' }, e.tool_call_id || DASH),
            h('td', null, e.channel_id || '')))))), { cls: 'sub' }) : null);
  });
}

function defenseEvidence(defense, runDefs) {
  const samples = Object.entries(defense || {});
  if (!samples.length) return muted('No defense evidence recorded for this run.');
  const typeOf = {};
  for (const d of runDefs || []) if (d && d.name) typeOf[d.name] = d.defense_type;
  return samples.map(([sample, dv]) => {
    const iv = (dv && dv.interventions) || [];
    const log = (dv && dv.log) || {};
    for (const d of (dv && dv.defenses) || []) if (d && d.name && !typeOf[d.name]) typeOf[d.name] = d.defense_type;
    const resolved = log.resolved_defenses || {};
    for (const [name, rd] of Object.entries(resolved)) {
      if (!typeOf[name] && rd && rd.config) typeOf[name] = rd.config.defense_type;
    }
    return h('div', { class: 'evidence' },
      h('h3', null, `sample ${sample}`),
      defenseCountsTable(iv, log, typeOf),
      kv([
        ['total activations', fmtInt(log.total_activations)],
        ['blocks in log', Array.isArray(log.blocks) ? fmtInt(log.blocks.length) : undefined],
        ['detections in log', Array.isArray(log.detections) ? fmtInt(log.detections.length) : undefined],
        ['true / false positives', log.true_positives != null ? `${fmtInt(log.true_positives)} / ${fmtInt(log.false_positives)}` : undefined],
        ['vaccinations', log.vaccinations ? fmtInt(Object.keys(log.vaccinations).length) : undefined],
        ['vaccination injections', log.vaccination_injections ? fmtInt(Object.keys(log.vaccination_injections).length) : undefined],
      ], 'kv-grid'),
      (log.vaccinations && Object.keys(log.vaccinations).length) || (log.vaccination_injections && Object.keys(log.vaccination_injections).length)
        ? lazyDetails('vaccinations (JSON)', () => h('pre', { class: 'pre' },
          JSON.stringify({ vaccinations: log.vaccinations, vaccination_injections: log.vaccination_injections }, null, 2)), { cls: 'sub' })
        : null,
      interventionsTable(iv));
  });
}

function defenseCountsTable(iv, log, typeOf) {
  const BOUNDS = ['model_input', 'model_output', 'tool_call'];
  const per = new Map();
  for (const x of iv) {
    const name = x.defense || '?';
    if (!per.has(name)) per.set(name, { checks: 0, blocks: 0, by: {} });
    const p = per.get(name);
    const b = x.boundary || '?';
    p.by[b] = p.by[b] || { checks: 0, blocks: 0 };
    p.checks++; p.by[b].checks++;
    if (x.allow === false) { p.blocks++; p.by[b].blocks++; }
  }
  const activity = log.activity || {};
  for (const name of Object.keys(activity)) if (!per.has(name)) per.set(name, { checks: 0, blocks: 0, by: {} });
  if (!per.size) return muted('No interventions recorded.');
  const extraBounds = [...new Set([...per.values()].flatMap(p => Object.keys(p.by)))].filter(b => !BOUNDS.includes(b));
  const bounds = [...BOUNDS, ...extraBounds];
  return h('div', null, h('div', { class: 'muted small' }, 'Boundary columns show blocks / checks, counted from the interventions below.'),
    h('div', { class: 'table-wrap' }, h('table', { class: 'data' },
    h('thead', null, h('tr', null,
      ['defense', 'type', 'checks', 'blocks'].map(x => h('th', { class: x === 'checks' || x === 'blocks' ? 'num' : '' }, x)),
      bounds.map(b => h('th', { class: 'num', title: `blocks / checks at ${b}` }, b)),
      h('th', { class: 'num' }, 'monitor model calls'),
      h('th', null, 'per agent (activity log)'))),
    h('tbody', null, [...per.entries()].map(([name, p]) => {
      const act = activity[name] || {};
      const agents = act.agents || {};
      return h('tr', null,
        h('td', { class: 'mono' }, name),
        h('td', { class: 'mono' }, typeOf[name] || DASH),
        h('td', { class: 'num' }, fmtInt(p.checks)),
        h('td', { class: 'num' }, p.blocks ? badge(fmtInt(p.blocks), 'blocked') : '0'),
        bounds.map(b => h('td', { class: 'num' }, p.by[b] ? `${fmtInt(p.by[b].blocks)} / ${fmtInt(p.by[b].checks)}` : DASH)),
        h('td', { class: 'num' }, fmtInt(act.model_calls)),
        h('td', { class: 'small per-agent' }, Object.keys(agents).length
          ? Object.entries(agents).map(([a, s]) => h('div', null,
            h('span', { class: 'mono' }, a), ': ', `${plural(s.checks || 0, 'check')}, ${plural(s.blocks || 0, 'block')}, ${plural(s.model_calls || 0, 'call')}`))
          : muted(DASH)));
    })))));
}

function interventionsTable(iv) {
  if (!iv.length) return muted('No interventions.');
  const st = { boundary: '', allow: '', defense: '', agent: '', q: '' };
  const bounds = [...new Set(iv.map(x => x.boundary).filter(Boolean))].sort();
  const defs = [...new Set(iv.map(x => x.defense).filter(Boolean))].sort();
  const agents = [...new Set(iv.map(x => x.agent).filter(Boolean))].sort();
  const count = h('span', { class: 'muted' });
  const rows = iv.map((x, i) => {
    const ref = x.tool_call_id || x.message_id || '';
    const tr = h('tr', { class: x.allow === false ? 'row-blocked' : '' },
      h('td', { class: 'num' }, String(i + 1)),
      h('td', { class: 'num' }, x.turn == null ? DASH : String(x.turn)),
      h('td', null, x.agent || DASH),
      h('td', { class: 'mono' }, x.defense || DASH),
      h('td', null, x.boundary || DASH),
      h('td', null, x.allow === false ? badge('BLOCK', 'blocked') : x.allow === true ? badge('allow', 'good') : muted(String(x.allow))),
      h('td', { class: 'mono' }, x.tool || x.tool_name || DASH),
      h('td', { class: 'reason' }, x.reason ? longText(x.reason, { lines: 6, chars: 1500 }) : muted(DASH)),
      h('td', { class: 'mono small', title: x.tool_call_id ? 'tool call id' : 'message id' }, ref || DASH));
    const hay = [x.reason, x.agent, x.defense, x.boundary, x.tool, ref].filter(Boolean).join(' ').toLowerCase();
    return { x, tr, hay };
  });
  const tbody = h('tbody', null, rows.map(r => r.tr));
  function apply() {
    const q = st.q.trim().toLowerCase();
    let n = 0;
    let blocks = 0;
    for (const r of rows) {
      const x = r.x;
      const ok = (!st.boundary || x.boundary === st.boundary) &&
        (!st.allow || (st.allow === 'block' ? x.allow === false : x.allow !== false)) &&
        (!st.defense || x.defense === st.defense) &&
        (!st.agent || x.agent === st.agent) &&
        (!q || r.hay.includes(q));
      r.tr.hidden = !ok;
      if (ok) { n++; if (x.allow === false) blocks++; }
    }
    count.textContent = `${fmtInt(n)} of ${fmtInt(rows.length)} interventions shown, ${fmtInt(blocks)} blocks`;
  }
  const search = h('input', { type: 'search', class: 'search small-search', placeholder: 'search reasons…', 'aria-label': 'Search interventions' });
  search.addEventListener('input', debounce(() => { st.q = search.value; apply(); }, 80));
  const filters = h('div', { class: 'filters' },
    labeled('boundary', select([['', 'all'], ...bounds], '', v => { st.boundary = v; apply(); })),
    labeled('decision', select([['', 'all'], ['block', 'blocked'], ['allow', 'allowed']], '', v => { st.allow = v; apply(); })),
    defs.length > 1 ? labeled('defense', select([['', 'all'], ...defs], '', v => { st.defense = v; apply(); })) : null,
    agents.length > 1 ? labeled('agent', select([['', 'all'], ...agents], '', v => { st.agent = v; apply(); })) : null,
    search, count);
  apply();
  return h('div', { class: 'interventions' },
    h('h4', null, 'Interventions'),
    filters,
    h('div', { class: 'table-wrap' }, h('table', { class: 'data compact' },
      h('thead', null, h('tr', null, ['#', 'turn', 'agent', 'defense', 'boundary', 'decision', 'tool', 'reason', 'message / call id']
        .map(x => h('th', { class: x === '#' || x === 'turn' ? 'num' : '' }, x)))),
      tbody)));
}

// =====================================================================================
// 7. View: transcript   #/run/<run>/episode/<episode>
// =====================================================================================

const STEP_KINDS = [
  ['', 'all steps'],
  ['agent', 'agent steps'],
  ['user', 'user steps'],
  ['system', 'system steps'],
  ['tools', 'steps with tool calls'],
  ['flagged', 'any flag below'],
  ['blocked', 'BLOCKED'],
  ['refusal', 'REFUSAL'],
  ['withheld', 'WITHHELD'],
  ['notice', 'provider-only notices'],
  ['error', 'tool errors'],
];

const FLAG_BADGE = {
  blocked: ['BLOCKED', 'blocked'],
  refusal: ['REFUSAL', 'refusal'],
  'refusal-text': ['REFUSAL TEXT (model-written)', 'plain'],
  withheld: ['WITHHELD', 'withheld'],
  notice: ['NOTICE', 'notice'],
  trailing: ['TRAILING-TURN FIX', 'notice'],
  rules: ['TURN RULES', 'notice'],
  error: ['ERROR', 'bad'],
};

function stepPreview(s, msg) {
  if (msg.trim()) return firstLine(msg, 100);
  const tc = (s.tool_calls || [])[0];
  if (tc) {
    let a = tc.arguments;
    if (a && typeof a === 'object') a = a.command || a.code || a.message || a.answer || JSON.stringify(a);
    return `${tc.function_name}: ${firstLine(asText(a), 90)}`;
  }
  if (s.reasoning_content) {
    const r = splitSignature(s.reasoning_content).text;
    return r ? '(reasoning) ' + firstLine(r.replace(/\*\*/g, ''), 90) : '(reasoning signature only)';
  }
  return '(empty)';
}

function resultView(r, orphan) {
  const rx = r.extra || {};
  const blocked = isBlockedResult(rx);
  const isErr = !blocked && (!!rx.is_error || !!rx.error);
  const withheld = !!rx.withheld_from_model_by_defense;
  const content = asText(r.content);
  return h('div', { class: ['result', blocked && 'is-blocked', isErr && 'is-error', withheld && 'is-withheld'] },
    h('div', { class: 'res-head' },
      h('span', { class: 'res-label' }, orphan ? 'result without a matching call' : 'result'),
      blocked ? badge('BLOCKED', 'blocked') : null,
      isErr ? badge('ERROR', 'bad') : null,
      withheld ? badge('WITHHELD', 'withheld', 'withheld from the model by a defense') : null,
      extraBadges(rx, ['is_error', 'error', 'withheld_from_model_by_defense', 'withheld_text']),
      h('span', { class: 'spacer' }),
      orphan && r.source_call_id ? h('span', { class: 'tc-id' }, r.source_call_id) : null),
    rx.error ? h('pre', { class: 'pre errtext' }, String(rx.error)) : null,
    rx.withheld_text ? lazyDetails('original content (never reached the model)', () => longText(asText(rx.withheld_text), { lines: 30 }), { cls: 'sub' }) : null,
    content ? longText(content, { lines: 30 }) : muted('(empty output)', 'small block'));
}

function toolCallView(fn, id, args, resultEls) {
  return h('div', { class: 'toolcall' },
    h('div', { class: 'tc-head' },
      h('span', { class: 'tc-label' }, 'tool call'),
      h('code', { class: 'tc-fn' }, fn || '?'),
      h('span', { class: 'spacer' }),
      id ? h('span', { class: 'tc-id' }, id) : null),
    argsView(fn, args),
    resultEls === null ? null
      : resultEls.length ? h('div', { class: 'tc-results' }, resultEls)
        : muted('(no result recorded)', 'small block'));
}

function renderStep(s) {
  const ex = s.extra || {};
  const results = (s.observation && s.observation.results) || [];
  const src = s.source || 'unknown';
  const msg = asText(s.message);
  const flags = new Set();
  if (ex.orbit_delivery_notice) flags.add('notice');
  if (ex.orbit_trailing_turn_fix) flags.add('trailing');
  if (ex.swarm_turn_rules) flags.add('rules');
  if (ex.withheld_from_model_by_defense) flags.add('withheld');
  if (ex.defense_refusal) flags.add('refusal');
  else if (ex.refusal_text_written_by_model || (src === 'agent' && msg.trim() === REFUSAL)) flags.add('refusal-text');
  for (const r of results) {
    const rx = r.extra || {};
    if (isBlockedResult(rx)) flags.add('blocked');
    else if (rx.is_error || rx.error) flags.add('error');
    if (rx.withheld_from_model_by_defense) flags.add('withheld');
  }
  const providerOnly = flags.has('notice') || flags.has('trailing') || flags.has('rules');
  const el = h('article', {
    class: ['step', 'src-' + src, providerOnly && 'provider-only', flags.has('refusal') && 'is-refusal',
      flags.has('blocked') && 'has-blocked', ex.withheld_from_model_by_defense && 'is-withheld'],
    id: `step-${s.step_id}`,
  });
  el.appendChild(h('header', { class: 'step-head' },
    h('span', { class: 'step-id' }, `#${s.step_id}`),
    h('span', { class: 'step-src' }, src),
    s.model_name ? muted(s.model_name, 'small') : null,
    [...flags].map(f => badge(...FLAG_BADGE[f])),
    extraBadges(ex, ['orbit_delivery_notice', 'orbit_trailing_turn_fix', 'swarm_turn_rules', 'withheld_from_model_by_defense', 'withheld_text',
      'defense_refusal', 'blocked_output_message_id', 'refusal_text_written_by_model']),
    h('span', { class: 'spacer' }),
    s.llm_call_count != null ? muted(plural(s.llm_call_count, 'model call'), 'small') : null));
  if (providerOnly) {
    el.appendChild(h('div', { class: 'caption caption-notice' },
      flags.has('rules')
        ? `Provider-only: ${ex.swarm_turn_rules} prompt placed by the turn rules, not part of the agent conversation.`
        : flags.has('trailing')
        ? 'Provider-only: continue prompt appended by the integration (trailing-turn fix), not part of the agent conversation.'
        : 'Provider-only: ORBIT delivery notice placed in the model input, not part of the agent conversation.'));
  }
  if (ex.withheld_from_model_by_defense) {
    el.appendChild(h('div', { class: 'caption caption-withheld' }, 'WITHHELD: a defense withheld this message from the model; the model received the placeholder shown below.'));
    if (ex.withheld_text) el.appendChild(lazyDetails('original content (never reached the model)', () => longText(asText(ex.withheld_text), { lines: 30 }), { cls: 'sub' }));
  }
  if (s.reasoning_content) el.appendChild(reasoningView(s.reasoning_content, 60));
  if (flags.has('refusal')) {
    el.appendChild(h('div', { class: 'caption caption-refusal' },
      'A defense blocked this completion and substituted the refusal sentence.'));
  } else if (flags.has('refusal-text')) {
    el.appendChild(h('div', { class: 'caption caption-notice' },
      'The model wrote the refusal sentence itself; no defense blocked this completion.'));
  }
  // Leading blank lines are dropped for display only.
  if (msg.trim()) el.appendChild(longText(msg.replace(/^\s*\n/, ''), { cls: 'msg-text' }));
  else if (!(s.tool_calls || []).length && !s.reasoning_content) el.appendChild(muted('(empty message)', 'small block'));

  const used = new Set();
  for (const tc of s.tool_calls || []) {
    const rs = results.filter(r => r.source_call_id === tc.tool_call_id);
    rs.forEach(r => used.add(r));
    el.appendChild(toolCallView(tc.function_name, tc.tool_call_id, tc.arguments, rs.map(r => resultView(r, false))));
  }
  for (const r of results) if (!used.has(r)) el.appendChild(resultView(r, true));

  const hay = [src, msg, splitSignature(s.reasoning_content).text,
    (s.tool_calls || []).map(tc => `${tc.function_name} ${asText(tc.arguments)}`).join('\n'),
    results.map(r => `${asText(r.content)} ${(r.extra && r.extra.error) || ''}`).join('\n')]
    .filter(Boolean).join('\n').toLowerCase();
  return { step: s, el, flags, hay, src, preview: stepPreview(s, msg), hasTools: !!(s.tool_calls || []).length };
}

function stepMatchesKind(e, k) {
  switch (k) {
    case '': return true;
    case 'agent': case 'user': case 'system': return e.src === k;
    case 'tools': return e.hasTools;
    case 'flagged': return e.flags.size > 0;
    case 'notice': return e.flags.has('notice') || e.flags.has('trailing') || e.flags.has('rules');
    default: return e.flags.has(k);
  }
}

async function viewEpisode({ run, episode, query: q }, view) {
  const [d, runs] = await Promise.all([
    api(P.transcript(run, episode)),
    api(P.runs()).catch(() => null),
  ]);
  const ep = d.episode || {};
  const t = d.transcript || {};
  const traj = t.trajectory || {};
  const steps = traj.steps || [];
  const orbit = t.orbit || {};

  const runSum = runs && runs.find(r => r.run === run);
  // Prev / next step only through episodes that have a transcript to show.
  const epNames = runSum ? (runSum.episodes || []).filter(e => e.has_transcript || e.episode === episode).map(e => e.episode) : [];
  const logOk = !runSum || !!runSum.eval_log_exists;
  const evalLog = runSum ? runSum.eval_log : null;
  const pos = epNames.indexOf(episode);
  const prev = pos > 0 ? epNames[pos - 1] : null;
  const next = pos >= 0 && pos < epNames.length - 1 ? epNames[pos + 1] : null;
  const navBtn = (name, label) => (name
    ? link(R.episode(run, name), label, 'btn btn-small')
    : h('span', { class: 'btn btn-small disabled' }, label));

  const entries = steps.map(renderStep);
  const counts = { tools: 0, blocked: 0, refusal: 0, withheld: 0, notice: 0, error: 0 };
  for (const e of entries) {
    counts.tools += (e.step.tool_calls || []).length;
    for (const f of ['blocked', 'refusal', 'withheld', 'error']) if (e.flags.has(f)) counts[f]++;
    if (e.flags.has('notice') || e.flags.has('trailing') || e.flags.has('rules')) counts.notice++;
  }

  const headerCard = h('section', { class: 'card ep-header' },
    h('div', { class: 'ep-title' },
      h('h1', null, episode),
      badge(ep.orbit_agent || t.agent || '?', 'agent'),
      ep.role ? roleBadge(ep.role) : null,
      t.model ? muted(t.model) : null,
      h('span', { class: 'spacer' }),
      navBtn(prev, `← ${prev || 'prev episode'}`),
      navBtn(next, `${next || 'next episode'} →`)),
    h('div', { class: 'links' },
      link(R.run(run), 'run page', 'btn btn-small'),
      link(R.timeline(run), 'timeline', 'btn btn-small'),
      logLink(logOk, evalLog, R.context(run, { agent: ep.orbit_agent || orbit.agent }), 'context windows for this agent', 'btn btn-small'),
      logLink(logOk, evalLog, R.sample(run), 'sample and workspace', 'btn btn-small')),
    episodeRecord(ep),
    h('div', { class: 'step-counts' },
      `${plural(steps.length, 'step')} · ${plural(counts.tools, 'tool call')}`,
      counts.blocked ? [' · ', badge(`${counts.blocked} BLOCKED`, 'blocked')] : null,
      counts.refusal ? [' · ', badge(`${counts.refusal} REFUSAL`, 'refusal')] : null,
      counts.withheld ? [' · ', badge(`${counts.withheld} WITHHELD`, 'withheld')] : null,
      counts.notice ? [' · ', badge(`${counts.notice} provider-only`, 'notice')] : null,
      counts.error ? [' · ', badge(`${counts.error} tool errors`, 'bad')] : null),
    h('h2', { class: 'sub-h' }, 'Judge verdict'),
    verdictBlock(ep.verdict),
    ep.error ? lazyDetails('episode error', () => longText(ep.error, { lines: 30 }), { cls: 'sub' }) : null,
    lazyDetails('ORBIT metadata and final metrics', () => h('div', null,
      kv([
        ['agent', orbit.agent], ['roster', asText(orbit.roster)], ['topology', orbit.topology],
        ['model calls', orbit.model_calls], ['submit recovered from events', asText(orbit.submit_recovered_from_events)],
        ['delivery notices recovered from events', asText(orbit.delivery_notices_recovered_from_events)],
        ['schema', traj.schema_version],
        ['agent version', ep.agent_version],
      ], 'kv-grid'),
      // Long values get the full width instead of a narrow grid cell.
      kv([
        ['eval log', orbit.eval_log ? mono(orbit.eval_log) : null],
        ['trajectory', traj.trajectory_id ? mono(traj.trajectory_id) : null],
        ['final metrics', traj.final_metrics ? h('pre', { class: 'pre' }, JSON.stringify(traj.final_metrics, null, 2)) : null],
      ])), { cls: 'sub' }),
    ep.final_text ? lazyDetails('final text', () => longText(ep.final_text, { lines: 40 }), { cls: 'sub' }) : null);

  // Sidebar: find box, kind filter, step list.
  const listBox = h('div', { class: 'side-list' });
  const countEl = h('div', { class: 'muted small side-count' });
  let active = null;
  const setActive = e => {
    if (active === e) return;
    if (active) { active.item.classList.remove('active'); active.item.removeAttribute('aria-current'); }
    active = e;
    if (!e) return;
    e.item.classList.add('active');
    e.item.setAttribute('aria-current', 'true');
    const it = e.item;
    if (it.offsetTop < listBox.scrollTop || it.offsetTop + it.offsetHeight > listBox.scrollTop + listBox.clientHeight) {
      listBox.scrollTop = it.offsetTop - listBox.clientHeight / 3;
    }
  };
  const jump = e => {
    e.el.scrollIntoView({ block: 'start' });
    e.el.classList.remove('flash');
    void e.el.offsetWidth;
    e.el.classList.add('flash');
    setActive(e);
  };
  for (const e of entries) {
    e.item = h('button', {
      type: 'button', class: ['step-item', 'src-' + e.src, e.flags.size && 'flagged'],
      title: `step ${e.step.step_id} (${e.src})`, onclick: () => jump(e),
    },
    h('span', { class: 'si-id' }, String(e.step.step_id)),
    h('span', { class: 'si-src' }, e.src === 'system' ? 'sys' : e.src),
    h('span', { class: 'si-text' }, e.preview),
    e.flags.size ? h('span', { class: 'si-flags' }, [...e.flags].map(f => badge(FLAG_BADGE[f][0][0], FLAG_BADGE[f][1], FLAG_BADGE[f][0]))) : null);
    listBox.appendChild(e.item);
  }

  const find = h('input', { type: 'search', class: 'search', placeholder: 'find in steps…', 'aria-label': 'Find in steps', value: q.get('find') || '' });
  const urlKind = q.get('kind') || '';
  let kindVal = STEP_KINDS.some(([k]) => k === urlKind) ? urlKind : '';
  const noteBox = h('div', { class: 'url-notes' });
  const showNote = text => noteBox.appendChild(h('div', { class: 'url-note' }, text));
  if (kindVal !== urlKind) showNote(`Unknown step kind “${urlKind}” in the link; showing all steps.`);
  const kindSel = select(STEP_KINDS, kindVal, v => { kindVal = v; applyFilter(); }, { 'aria-label': 'Step kind' });
  let live = false;
  function updateUrl() {
    if (!live) return;
    const target = R.episode(run, episode, { find: find.value.trim(), kind: kindVal, step: q.get('step') });
    setHashQuietly(target);
  }
  function applyFilter() {
    const needle = find.value.trim().toLowerCase();
    let n = 0;
    for (const e of entries) {
      const ok = (!needle || e.hay.includes(needle)) && stepMatchesKind(e, kindVal);
      e.el.hidden = !ok;
      e.item.hidden = !ok;
      if (ok) n++;
    }
    countEl.textContent = `${fmtInt(n)} of ${plural(entries.length, 'step')} shown`;
    updateUrl();
  }
  find.addEventListener('input', debounce(applyFilter, 120));
  find.addEventListener('keydown', ev => {
    if (ev.key === 'Enter') {
      const first = entries.find(e => !e.el.hidden);
      if (first) jump(first);
    }
  });
  applyFilter();

  const side = h('aside', { class: 'sidebar' },
    h('div', { class: 'side-tools' }, find, kindSel, noteBox, countEl),
    listBox);
  const stepsBox = h('div', { class: 'steps' }, entries.length ? entries.map(e => e.el) : muted('This transcript has no steps.'));
  const page = h('div', { class: 'split' }, side, h('div', { class: 'main-col' }, headerCard, stepsBox));

  // Highlight the step at the top of the viewport in the sidebar.
  const visible = new Set();
  const io = new IntersectionObserver(obs => {
    for (const o of obs) { if (o.isIntersecting) visible.add(o.target); else visible.delete(o.target); }
    const top = entries.find(e => visible.has(e.el));
    if (top) setActive(top);
  }, { rootMargin: '-60px 0px -65% 0px' });
  for (const e of entries) io.observe(e.el);
  view.cleanup(() => { live = false; io.disconnect(); });
  view.mounted(() => {
    live = true;
    document.title = `${episode} · ${run} · results`;
    updateUrl();
  });

  const startStep = q.get('step');
  if (startStep) {
    view.mounted(() => {
      const e = entries.find(x => String(x.step.step_id) === startStep);
      if (e) jump(e);
      else showNote(`Step “${startStep}” is not in this transcript.`);
    });
  }
  return page;
}

// =====================================================================================
// 8. View: context windows   #/run/<run>/context?agent=<name>&call=<i>
// =====================================================================================

// A call's flags are the union of its input messages' flags (plus the output being the
// refusal sentence). They describe the history the call saw, not what happened to its output.
const CALL_FLAG_LABEL = {
  delivery_notice: ['notice in input', 'notice', 'an ORBIT board delivery notice is in this call’s input'],
  trailing_turn_fix: ['continue prompt in input', 'notice', 'the integration appended a continue prompt (trailing-turn fix) to this input'],
  turn_rules: ['turn rules in input', 'notice', 'the turn rules placed a planning prompt, turn header or continue prompt in this input'],
  redacted: ['redacted input', 'withheld', 'a defense withheld a message of this input from the model'],
  refusal_in_history: ['refusal in history', 'plain', 'the input holds the refusal sentence: a substituted completion or the model’s own text'],
  output_blocked: ['output blocked', 'blocked', 'a defense blocked this call’s completion and substituted the refusal sentence'],
};
const CALL_FILTERS = [
  ['', 'any'],
  ['output_blocked', 'output blocked by a defense'],
  ['delivery_notice', 'notice in input'],
  ['trailing_turn_fix', 'continue prompt in input'],
  ['turn_rules', 'turn rules in input'],
  ['redacted', 'redacted input'],
  ['refusal_in_history', 'refusal in history'],
  ['error', 'has error'],
];
const MSG_FLAG_BADGE = {
  delivery_notice: ['delivery notice', 'notice'],
  trailing_turn_fix: ['trailing-turn fix', 'notice'],
  turn_rules: ['turn rules', 'notice'],
  redacted: ['REDACTED', 'withheld'],
  refusal_in_history: ['refusal in history', 'plain'],
  output_blocked: ['BLOCKED OUTPUT', 'refusal'],
};

function msgFlagBadge(f) {
  const [label, kind] = MSG_FLAG_BADGE[f] || [f, 'plain'];
  return badge(label, kind, f);
}

function callFlagBadge(f) {
  const [label, kind, title] = CALL_FLAG_LABEL[f] || [f, 'plain', f];
  return badge(label, kind, title);
}

function callWho(c) {
  if (c.defense) return `${c.defense}${c.agent ? ' on ' + c.agent : ''}`;
  return c.agent || '(no agent)';
}

function usageText(u) {
  if (!u) return 'no usage';
  const parts = [`in ${fmtCompact(u.input)}`, `out ${fmtCompact(u.output)}`];
  if (u.cache_read) parts.push(`cache ${fmtCompact(u.cache_read)}`);
  return parts.join(' · ');
}

function blockReasons(ivs) {
  return ivs.map(x => `${x.defense || '?'} at ${x.boundary || '?'}: ${x.reason || '(no reason recorded)'}`).join('\n');
}

/**
 * Blocks from the run's defense evidence, indexed for the context view.
 * A model_output intervention carries the message id of the completion it checked, which is
 * the output id of the model call that produced it. The call index has no output ids, so the
 * sidebar aligns each agent's own calls with that agent's checked completions in order, and
 * only when the two counts agree. Opening a call checks the alignment against the real id.
 */
function blockIndex(detail, calls) {
  // Blocked outputs come from the API (call.output_blocked, matched by the raw output id on
  // the server); interventions are kept by message id for the reasons shown inside a call.
  const outBlocks = new Map();   // completion message id -> blocking interventions
  const toolBlocks = new Map();  // tool call id -> blocking interventions
  const add = (m, k, x) => { if (!m.has(k)) m.set(k, []); m.get(k).push(x); };
  for (const dv of Object.values((detail && detail.defense) || {})) {
    for (const x of (dv && dv.interventions) || []) {
      if (x.boundary === 'model_output' && x.message_id && x.allow === false) add(outBlocks, x.message_id, x);
      else if (x.boundary === 'tool_call' && x.allow === false && x.tool_call_id) add(toolBlocks, x.tool_call_id, x);
    }
  }
  const blockedCalls = new Map(); // call index -> blocking interventions (unknown by id here)
  for (const c of calls) if (c.output_blocked) blockedCalls.set(c.index, []);
  return { outBlocks, toolBlocks, callOut: new Map(), blockedCalls, misaligned: [], distrusted: false };
}

function blockedToolResult(ivs) {
  const notRun = ivs.some(x => x.tool_executed === false);
  return h('div', { class: 'result is-blocked' },
    h('div', { class: 'res-head' },
      h('span', { class: 'res-label' }, notRun ? 'not executed' : 'tool call blocked'),
      badge('BLOCKED', 'blocked')),
    h('pre', { class: 'pre errtext' }, blockReasons(ivs)));
}

function messageView(m, i, status, opts = {}) {
  const flags = m.flags || [];
  const notice = flags.includes('delivery_notice') || flags.includes('trailing_turn_fix') || flags.includes('turn_rules');
  const el = h('div', {
    class: ['msg', 'role-' + m.role, status && 'st-' + status, notice && 'provider-only',
      flags.includes('redacted') && 'is-redacted', flags.includes('output_blocked') && 'is-refusal'],
    id: i != null ? `msg-${i}` : null,
  });
  el.appendChild(h('div', { class: 'msg-head' },
    i != null ? h('span', { class: 'step-id' }, `[${i}]`) : null,
    h('span', { class: 'msg-role' }, m.role),
    status === 'new' ? badge('NEW', 'new', 'not in the previous call of this caller') : null,
    status === 'prev-out' ? badge('previous output', 'prevout', 'the output of the previous call of this caller') : null,
    status === 'replaced' ? badge('replaces blocked output', 'blocked', 'this refusal took the place of the previous call’s blocked completion') : null,
    flags.map(msgFlagBadge),
    m.role === 'tool' && m.function ? h('code', { class: 'tc-fn' }, m.function) : null,
    m.role === 'assistant' && m.model ? muted(m.model, 'small') : null,
    h('span', { class: 'spacer' }),
    m.tool_call_id ? h('span', { class: 'tc-id', title: 'tool call id' }, m.tool_call_id) : null,
    m.id ? h('span', { class: 'tc-id', title: 'message id' }, m.id) : null));
  if (notice) {
    el.appendChild(h('div', { class: 'caption caption-notice' },
      flags.includes('turn_rules')
        ? 'Provider-only: planning prompt, turn header or continue prompt placed by the turn rules.'
        : flags.includes('trailing_turn_fix')
        ? 'Provider-only: continue prompt appended by the integration.'
        : 'Provider-only: ORBIT board delivery notice placed in the input.'));
  }
  if (flags.includes('redacted')) {
    el.appendChild(h('div', { class: 'caption caption-withheld' }, 'REDACTED: a defense withheld this message’s content from the model; the placeholder below is what the provider received.'));
    if (m.withheld_text) el.appendChild(lazyDetails('original content (never reached the model)', () => longText(asText(m.withheld_text), { lines: 30 }), { cls: 'sub' }));
  }
  if (m.error) el.appendChild(h('pre', { class: 'pre errtext' }, String(m.error)));
  if (m.reasoning) el.appendChild(reasoningView(m.reasoning, 40));
  const text = asText(m.text);
  if (text) el.appendChild(longText(text, { lines: 30, cls: 'msg-text' }));
  else if (!(m.tool_calls || []).length) el.appendChild(muted('(empty)', 'small block'));
  for (const tc of m.tool_calls || []) {
    const blocks = opts.toolBlocks ? opts.toolBlocks.get(tc.id) : null;
    el.appendChild(toolCallView(tc.function, tc.id, tc.arguments, blocks ? [blockedToolResult(blocks)] : null));
  }
  return el;
}

async function viewContext({ run, query: q }, view) {
  const [idx, detail] = await Promise.all([
    api(P.context(run)),
    api(P.run(run)).catch(() => null),
  ]);
  const calls = idx.calls || [];
  const byIndex = new Map(calls.map(c => [c.index, c]));
  const bi = blockIndex(detail, calls);
  let live = false;

  const noteBox = h('div', { class: 'url-notes' });
  const showNote = text => noteBox.appendChild(h('div', { class: 'url-note' }, text));

  const agentNames = [...new Set([...(idx.agents || []), ...calls.map(c => c.agent).filter(Boolean)])].sort();
  const agentOpts = [['', 'all agents'], ...agentNames.map(a => [a, a])];
  if (calls.some(c => !c.agent)) agentOpts.push(['(none)', '(no agent)']);
  const nDef = calls.filter(c => c.defense).length;

  // Filters from the URL. An unknown value falls back to "all", with a note saying so.
  const st = { agent: '', kind: '', flag: '', selected: null, onlyNew: false };
  const urlAgent = q.get('agent') || '';
  if (urlAgent && agentOpts.some(([v]) => v === urlAgent)) st.agent = urlAgent;
  else if (urlAgent) showNote(`Unknown agent “${urlAgent}” in the link; showing all agents.`);
  const urlKind = q.get('kind') || '';
  if (urlKind === 'agent' || urlKind === 'defense') st.kind = urlKind;
  else if (urlKind) showNote(`Unknown call kind “${urlKind}” in the link; showing all calls.`);
  const urlFlag = q.get('flag') || '';
  if (urlFlag && CALL_FILTERS.some(([v]) => v === urlFlag)) st.flag = urlFlag;
  else if (urlFlag) showNote(`Unknown flag “${urlFlag}” in the link; showing calls with any flag.`);
  if (!detail) showNote('Could not load the run’s defense evidence, so blocked outputs are not marked.');
  if (bi.misaligned.length) {
    showNote(`Blocked outputs are marked in this list only where an agent’s calls line up with its defense checks. ` +
      `Not aligned: ${bi.misaligned.join('; ')}. Opening a call still shows its blocks.`);
  }

  const items = calls.map(c => {
    const blocked = bi.blockedCalls.get(c.index);
    const blockedBadge = blocked ? badge('OUTPUT BLOCKED', 'blocked', blockReasons(blocked)) : null;
    if (blockedBadge) blockedBadge.classList.add('ci-blocked');
    const el = h('button', {
      type: 'button', class: ['call-item', c.defense ? 'is-defense' : 'is-agent', c.error && 'has-error', blocked && 'is-blocked-call'],
      onclick: () => selectCall(c.index, { fromList: true }),
    },
    h('div', { class: 'ci-line' },
      h('span', { class: 'ci-idx' }, `#${c.index}`),
      h('span', { class: 'ci-who' }, c.agent || '(no agent)'),
      c.defense ? badge(c.defense, 'def') : badge('agent call', 'agent'),
      blockedBadge),
    h('div', { class: 'ci-line ci-meta' },
      `${plural(c.n_input, 'msg')} · ${usageText(c.usage)}`,
      (c.output_tool_calls || []).length ? ` · → ${c.output_tool_calls.join(', ')}` : ''),
    c.output_preview && !(c.output_tool_calls || []).length
      ? h('div', { class: 'ci-line ci-preview' }, firstLine(c.output_preview, 70)) : null,
    (c.flags || []).length || c.error
      ? h('div', { class: 'ci-line' }, (c.flags || []).map(callFlagBadge), c.error ? badge('error', 'bad', c.error) : null)
      : null);
    return { c, el };
  });

  const listBox = h('div', { class: 'side-list' }, items.map(it => it.el));
  const countEl = h('div', { class: 'muted small side-count' });
  const detailEl = h('div', { class: 'call-detail' });
  const blockedCount = h('span', { class: 'text-bad' });

  const visibleItems = () => items.filter(it => !it.el.hidden);
  function matches(c) {
    return (!st.agent || (st.agent === '(none)' ? !c.agent : c.agent === st.agent)) &&
      (!st.kind || (st.kind === 'agent' ? !c.defense : !!c.defense)) &&
      (!st.flag || (st.flag === 'error' ? !!c.error
        : st.flag === 'output_blocked' ? bi.blockedCalls.has(c.index)
          : (c.flags || []).includes(st.flag)));
  }
  function applyFilter() {
    let n = 0;
    for (const it of items) {
      const ok = matches(it.c);
      it.el.hidden = !ok;
      if (ok) n++;
    }
    countEl.textContent = `${fmtInt(n)} of ${plural(calls.length, 'call')} shown`;
    blockedCount.textContent = bi.blockedCalls.size ? ` · ${plural(bi.blockedCalls.size, 'output')} blocked` : '';
    updateUrl();
  }

  function updateUrl() {
    if (!live) return;
    const target = R.context(run, {
      agent: st.agent, kind: st.kind, flag: st.flag, call: st.selected == null ? '' : st.selected,
    });
    setHashQuietly(target);
  }

  // If an opened call's real output id disagrees with the order alignment, drop the marks.
  function checkAlignment() { /* blocked outputs are matched by id on the server */ }

  function scrollItemIntoList(it) {
    const e = it.el;
    if (e.offsetTop < listBox.scrollTop || e.offsetTop + e.offsetHeight > listBox.scrollTop + listBox.clientHeight) {
      listBox.scrollTop = e.offsetTop - listBox.clientHeight / 3;
    }
  }

  function prevSameCaller(c) {
    for (let j = c.index - 1; j >= 0; j--) {
      const p = byIndex.get(j);
      if (p && p.agent === c.agent && p.defense === c.defense) return p;
    }
    return null;
  }

  let selToken = 0;
  async function selectCall(i, opts = {}) {
    const c = byIndex.get(i);
    if (!c) return;
    st.selected = i;
    for (const it of items) { it.el.classList.toggle('active', it.c.index === i); if (it.c.index === i) it.el.setAttribute('aria-current', 'true'); else it.el.removeAttribute('aria-current'); }
    const it = items.find(x => x.c.index === i);
    if (it && !opts.fromList) scrollItemIntoList(it);
    if (it && opts.focus) it.el.focus({ preventScroll: true });
    updateUrl();
    const tok = ++selToken;
    detailEl.replaceChildren(h('div', { class: 'loading' }, `Loading call #${i}…`));
    // Holding j or k skips through calls; only the call the user stops on is fetched.
    if (opts.debounce) {
      await new Promise(res => setTimeout(res, 90));
      if (tok !== selToken || !live) return;
    }
    const prev = prevSameCaller(c);
    try {
      const [full, prevFull] = await Promise.all([
        apiCall(P.call(run, i)),
        prev ? apiCall(P.call(run, prev.index)).catch(e => ({ __error: e })) : Promise.resolve(null),
      ]);
      if (tok !== selToken || !live) return;
      checkAlignment(i, full);
      detailEl.replaceChildren(callDetail(c, full, prev, prevFull, selectCall, { bi, st }));
      const top = detailEl.getBoundingClientRect().top;
      if (top < 0 || top > window.innerHeight) window.scrollTo(0, 0);
      else if (window.scrollY > 0 && opts.scrollTop !== false) window.scrollTo(0, 0);
    } catch (e) {
      if (tok !== selToken || !live) return;
      detailEl.replaceChildren(errorBox(e, `call #${i}`));
    }
  }

  function move(dir, focus) {
    const vis = visibleItems();
    if (!vis.length) return;
    let next;
    const pos = vis.findIndex(it => it.c.index === st.selected);
    if (pos >= 0) next = vis[Math.min(vis.length - 1, Math.max(0, pos + dir))];
    else if (st.selected == null) next = dir > 0 ? vis[0] : vis[vis.length - 1];
    // The selected call is filtered out: go to the nearest visible call in that direction.
    else if (dir > 0) next = vis.find(x => x.c.index > st.selected) || vis[vis.length - 1];
    else next = [...vis].reverse().find(x => x.c.index < st.selected) || vis[0];
    if (next && next.c.index !== st.selected) selectCall(next.c.index, { debounce: true, focus });
  }

  // j / k work anywhere on the page. The arrow keys move between calls only while the call
  // list has focus, so elsewhere they still scroll a long call.
  const onKey = e => {
    if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey) return;
    const tgt = e.target;
    if (tgt && (tgt.tagName === 'INPUT' || tgt.tagName === 'SELECT' || tgt.tagName === 'TEXTAREA' || tgt.isContentEditable)) return;
    const inList = tgt instanceof Node && listBox.contains(tgt);
    let dir = 0;
    if (e.key === 'j' || (inList && e.key === 'ArrowDown')) dir = 1;
    else if (e.key === 'k' || (inList && e.key === 'ArrowUp')) dir = -1;
    else return;
    e.preventDefault();
    move(dir, inList);
  };

  const kindBtns = h('div', { class: 'seg', role: 'group', 'aria-label': 'call kind' },
    [['', 'all'], ['agent', 'agent calls only'], ['defense', 'defense calls only']].map(([v, label]) => {
      const b = h('button', { type: 'button', class: ['seg-btn', st.kind === v && 'active'], 'aria-pressed': st.kind === v ? 'true' : 'false', dataset: { v } }, label);
      b.addEventListener('click', () => {
        st.kind = v;
        for (const x of kindBtns.children) { x.classList.toggle('active', x.dataset.v === v); x.setAttribute('aria-pressed', x.dataset.v === v ? 'true' : 'false'); }
        applyFilter();
      });
      return b;
    }));

  const side = h('aside', { class: 'sidebar' },
    h('div', { class: 'side-tools' },
      h('div', { class: 'side-title' }, h('strong', null, 'Model calls'), ' ',
        muted(`${fmtInt(calls.length - nDef)} agent, ${fmtInt(nDef)} defense`), blockedCount),
      labeled('agent', select(agentOpts, st.agent, v => { st.agent = v; applyFilter(); })),
      kindBtns,
      labeled('flag', select(CALL_FILTERS, st.flag, v => { st.flag = v; applyFilter(); })),
      h('div', { class: 'muted small' }, 'keys: j / k move between calls; ↓ / ↑ do too while the list has focus'),
      noteBox,
      countEl),
    listBox);

  applyFilter();
  const page = h('div', { class: 'split' }, side, h('div', { class: 'main-col' }, detailEl));

  view.mounted(() => {
    live = true;
    document.title = `context · ${run} · results`;
    document.addEventListener('keydown', onKey);
  });
  view.cleanup(() => {
    live = false;
    document.removeEventListener('keydown', onKey);
  });

  if (!calls.length) {
    detailEl.replaceChildren(muted('The eval log has no model calls.'));
    view.mounted(updateUrl);
    return page;
  }
  const want = q.get('call');
  const wanted = want && /^\d+$/.test(want) && byIndex.has(Number(want)) ? Number(want) : null;
  const vis = visibleItems();
  const start = wanted != null ? wanted : vis.length ? vis[0].c.index : null;
  if (want && wanted == null) {
    showNote(`Call “${want}” does not exist; this run has calls #0 to #${calls[calls.length - 1].index}.` +
      (start != null ? ` Showing call #${start}.` : ''));
  }
  if (start == null) {
    detailEl.replaceChildren(muted('No calls match these filters.'));
    view.mounted(updateUrl);
  } else {
    view.mounted(() => selectCall(start));
  }
  return page;
}

function callDetail(c, full, prev, prevFull, selectCall, ctx) {
  const { bi, st } = ctx;
  const input = full.input || [];
  const outBlocks = full.output && full.output.id ? bi.outBlocks.get(full.output.id) || null : null;
  // Diff against the previous call by the same caller (same agent and same defense).
  const status = new Array(input.length).fill(null);
  let hint;
  if (!prev) {
    hint = h('div', { class: 'diff-hint' }, `First call by ${callWho(c)}: there is no earlier call to compare with.`);
  } else if (!prevFull || prevFull.__error) {
    hint = h('div', { class: 'diff-hint' }, `Could not load call #${prev.index} to compare with.`,
      prevFull && prevFull.__error ? errorBox(prevFull.__error) : null);
  } else {
    const prevIds = new Set((prevFull.input || []).map(m => m.id).filter(Boolean));
    const prevOut = prevFull.output && prevFull.output.id;
    const prevBlocks = prevOut ? bi.outBlocks.get(prevOut) || null : null;
    const cur = new Set(input.map(m => m.id).filter(Boolean));
    let nNew = 0;
    let nKept = 0;
    let nOut = 0;
    let replacedAt = -1;
    input.forEach((m, i) => {
      if (!m.id) return;
      if (m.id === prevOut) { status[i] = 'prev-out'; nOut++; }
      else if (prevIds.has(m.id)) { status[i] = 'kept'; nKept++; }
      else if (prevBlocks && replacedAt < 0 && (m.flags || []).includes('refusal_in_history')) { status[i] = 'replaced'; replacedAt = i; }
      else { status[i] = 'new'; nNew++; }
    });
    const dropped = [...prevIds].filter(id => !cur.has(id)).length;
    const blockers = prevBlocks ? [...new Set(prevBlocks.map(x => x.defense || '?'))].join(', ') : '';
    hint = h('div', { class: 'diff-hint' },
      'Compared with ',
      h('button', { type: 'button', class: 'linkish', onclick: () => selectCall(prev.index) }, `call #${prev.index}`),
      `, the previous call by ${callWho(c)}: `,
      badge(`${nNew} new`, 'new'), ' ',
      `${fmtInt(nKept)} carried over`,
      nOut ? `, ${nOut} is that call’s output` : '',
      dropped ? `, ${plural(dropped, 'message')} from that input no longer present` : '',
      '.',
      prevBlocks ? h('div', { class: 'diff-blocked' },
        badge('BLOCKED', 'blocked'), ` ${blockers} blocked the output of call #${prev.index}`,
        replacedAt >= 0 ? `; the refusal at [${replacedAt}] took its place.` : ', and it does not appear in this input.') : null);
  }

  const firstNew = status.findIndex(s => s === 'new' || s === 'replaced');
  const msgs = h('div', { class: ['msgs', prev && st.onlyNew && 'only-new'] },
    input.map((m, i) => messageView(m, i, status[i], { toolBlocks: bi.toolBlocks })));
  const onlyNew = h('input', { type: 'checkbox', checked: st.onlyNew });
  onlyNew.addEventListener('change', () => {
    st.onlyNew = onlyNew.checked;
    msgs.classList.toggle('only-new', onlyNew.checked);
  });

  const u = full.usage || {};
  const head = h('section', { class: 'card call-head' },
    h('div', { class: 'ep-title' },
      h('h1', null, `Call #${c.index}`),
      c.defense ? badge(c.defense, 'def') : badge('agent call', 'agent'),
      h('span', null, c.agent || '(no agent)'),
      outBlocks ? badge('OUTPUT BLOCKED', 'blocked', blockReasons(outBlocks)) : null,
      (full.flags || []).map(callFlagBadge),
      full.error ? badge('error', 'bad') : null),
    kv([
      ['model', full.model],
      ['timestamp', fmtTime(full.timestamp)],
      ['working time', fmtSecs(full.working_time)],
      ['stop reason', full.stop_reason],
      ['input messages', `${fmtInt(full.n_input)} (${fmtInt(full.input_chars)} chars)`],
      ['usage input', fmtInt(u.input)],
      ['usage cache read', fmtInt(u.cache_read)],
      ['usage output', fmtInt(u.output)],
      ['usage reasoning', fmtInt(u.reasoning)],
      ['output tool calls', (full.output_tool_calls || []).join(', ') || null],
    ], 'kv-grid'),
    full.error ? h('pre', { class: 'pre errtext' }, String(full.error)) : null,
    full.output_error ? h('pre', { class: 'pre errtext' }, `output error: ${full.output_error}`) : null,
    hint);

  const inputSec = h('section', { class: 'card' },
    h('div', { class: 'sec-head' },
      h('h2', null, `Input (${plural(input.length, 'message')})`),
      h('span', { class: 'spacer' }),
      firstNew >= 0 ? h('button', {
        type: 'button', class: 'btn btn-small',
        onclick: () => { const el = document.getElementById(`msg-${firstNew}`); if (el) el.scrollIntoView({ block: 'start' }); },
      }, 'jump to first new') : null,
      prev ? h('label', { class: 'field check' }, onlyNew, h('span', null, 'only new and previous output')) : null),
    input.length ? msgs : muted('No input messages.'));

  const outputSec = h('section', { class: ['card', outBlocks && 'card-blocked'] },
    h('h2', null, 'Output'),
    outBlocks ? h('div', { class: 'caption caption-blocked' },
      h('strong', null, 'BLOCKED. '),
      'A defense blocked this completion at the model output. Its text and tool calls were dropped, ' +
      'and the conversation continued with the refusal sentence in its place.',
      h('pre', { class: 'pre errtext' }, blockReasons(outBlocks))) : null,
    full.output ? messageView(full.output, null, null, { toolBlocks: bi.toolBlocks }) : muted('No output message.'));

  const schemas = full.tool_schemas || [];
  const toolsSec = h('section', { class: 'card' },
    h('h2', null, `Tools offered (${schemas.length})`),
    schemas.length ? schemas.map(s => lazyDetails(s.name, () => longText(s.description || '(no description)', { lines: 30 }), { cls: 'sub' }))
      : muted('No tools were offered in this call.'));

  return h('div', null, head, inputSec, outputSec, toolsSec);
}

// =====================================================================================
// 9. View: sample   #/run/<run>/sample
// =====================================================================================

const META_ORDER = ['listing', 'git', 'agent_evals', 'agent_evals_main', 'probe'];

function probeView(probe) {
  if (!probe || typeof probe !== 'object' || Array.isArray(probe)) return h('pre', { class: 'pre' }, asText(probe));
  return h('div', { class: 'probe' }, Object.entries(probe).map(([k, v]) => {
    if (v && typeof v === 'object' && ('out' in v || 'rc' in v)) {
      return h('div', { class: 'probe-item' },
        h('div', { class: 'cfg-key' }, h('span', { class: 'mono' }, k), ' ',
          badge(`rc ${v.rc}`, v.rc === 0 ? 'good' : 'bad')),
        longText(asText(v.out), { lines: 30 }),
        v.err ? h('pre', { class: 'pre errtext' }, asText(v.err)) : null);
    }
    return h('div', { class: 'probe-item' }, h('div', { class: 'cfg-key mono' }, k), h('pre', { class: 'pre' }, asText(v)));
  }));
}

async function viewSample({ run }, view) {
  const s = await api(P.sample(run));
  view.mounted(() => { document.title = `sample · ${run} · results`; });
  const lim = s.limit;
  const facts = section('Sample', kv([
    ['status', s.status ? badge(s.status, s.status === 'success' ? 'good' : 'bad') : null],
    ['model', s.model],
    ['eval id', s.eval_id ? mono(s.eval_id) : null],
    ['sample', s.sample_id != null ? `${s.sample_id} (epoch ${s.epoch})` : null],
    ['total time', fmtSecs(s.total_time)],
    ['working time', fmtSecs(s.working_time)],
    ['limit', lim ? `${lim.type}${lim.limit != null ? ' ' + lim.limit : ''}${lim.message ? ': ' + lim.message : ''}` : null],
    ['messages', fmtInt(s.n_messages)],
    ['events', fmtInt(s.n_events)],
  ], 'kv-grid'),
  s.error ? [h('h3', { class: 'sub-h' }, 'Error'), longText(s.error, { lines: 30, cls: 'errtext' })] : null);

  const usage = Object.entries(s.model_usage || {});
  const usageSec = section('Model usage', usage.length ? h('div', { class: 'table-wrap' }, h('table', { class: 'data' },
    h('thead', null, h('tr', null, ['model', 'input', 'cache read', 'output', 'reasoning'].map((x, i) => h('th', { class: i ? 'num' : '' }, x)))),
    h('tbody', null, usage.map(([m, u]) => h('tr', null,
      h('td', { class: 'mono' }, m),
      ['input', 'cache_read', 'output', 'reasoning'].map(k => h('td', { class: 'num' }, fmtInt(u && u[k])))))))) : muted('No usage recorded.'));

  const scores = Object.entries(s.scores || {});
  const scoreSecs = scores.length ? scores.map(([name, sc]) => {
    const md = (sc && sc.metadata) || {};
    const keys = [...META_ORDER.filter(k => k in md), ...Object.keys(md).filter(k => !META_ORDER.includes(k))];
    return section(`Score: ${name}`,
      kv([
        ['value', asText(sc.value)],
        ['explanation', sc.explanation],
      ], 'kv-grid'),
      h('h3', { class: 'sub-h' }, 'Answer (message.txt)'),
      sc.answer ? longText(sc.answer, { lines: 40 }) : muted('No answer.'),
      keys.map(k => h('div', { class: 'meta-block' },
        h('h3', { class: 'sub-h' }, k),
        k === 'probe' ? probeView(md[k]) : longText(asText(md[k]), { lines: 60, chars: 40000 }))));
  }) : section('Scores', muted('No scores (the sample did not reach the scorer).'));

  const storeSec = s.store
    ? section('Store', lazyDetails('sample store (JSON)', () => longText(JSON.stringify(s.store, null, 2), { lines: 80, chars: 60000 }), { cls: 'sub' }))
    : null;

  return h('div', { class: 'page' },
    h('div', { class: 'page-head' },
      h('h1', null, `${run}: sample and workspace`),
      h('div', { class: 'links' }, link(R.run(run), 'run page', 'btn btn-small'), link(R.context(run), 'context windows', 'btn btn-small'))),
    facts, usageSec, scoreSecs, storeSec);
}

// =====================================================================================
// 10. Chart helpers: SVG builder, role glyphs, tooltip, colour ramp, axis ticks
// =====================================================================================

const SVG_NS = 'http://www.w3.org/2000/svg';
let vizUid = 0;
const r2 = n => Math.round(n * 100) / 100;

/** Build an SVG element. Strings in children become text nodes; attrs never take markup. */
function svgEl(tag, attrs, ...children) {
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
const ROLE_ORDER = ['disregulator', 'worker', 'regulator'];
const ROLE_PLURAL = { disregulator: 'disregulators', worker: 'workers', regulator: 'regulators' };

/** A role glyph centred on (cx, cy). cls replaces the role fill class (outcome fills in the cascade). */
function roleGlyph(role, cx, cy, r, cls) {
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

function roleIcon(role, size = 12) {
  return svgEl('svg', { class: 'role-icon', width: size, height: size, viewBox: `0 0 ${size} ${size}`, 'aria-hidden': 'true', focusable: 'false' },
    roleGlyph(role, size / 2, size / 2 + (role === 'disregulator' ? 0.8 : 0), size / 2 - 1.6));
}

/** One floating tooltip for every chart. Lines are strings or nodes, inserted as text. */
const tip = (() => {
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
function withTip(el, lines) {
  el.addEventListener('pointermove', ev => tip.show(ev, lines()));
  el.addEventListener('pointerleave', () => tip.hide());
  return el;
}

function cssVar(name, fallback) {
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
function seqColor(frac) { return mixOklab(cssVar('--seq-lo', '#fde8e5'), cssVar('--seq-hi', '#a3170e'), Math.min(1, Math.max(0, frac))); }
/** Ink or white, whichever reads better on a fill. */
function inkOn(hex) {
  const [r, g, b] = hexRgb(hex);
  const lum = 0.2126 * toLin(r) + 0.7152 * toLin(g) + 0.0722 * toLin(b);
  return lum > 0.179 ? '#1c1c1a' : '#ffffff';
}

const TIME_STEPS = [0.1, 0.2, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1200, 1800, 3600, 7200];
const COUNT_STEPS = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000];
function ticksFor(lo, hi, maxTicks, steps) {
  const span = hi - lo;
  if (!(span > 0)) return [lo];
  const step = steps.find(s => span / s <= maxTicks) || steps[steps.length - 1];
  const out = [];
  for (let v = Math.ceil(lo / step - 1e-9) * step; v <= hi + 1e-9 && out.length < 200; v += step) out.push(+v.toFixed(6));
  return out;
}
function fmtTick(s) {
  if (s < 60) return `${+s.toFixed(2)}s`;
  const m = Math.floor(s / 60);
  const r = Math.round(s - m * 60);
  if (m < 60) return r ? `${m}m${String(r).padStart(2, '0')}` : `${m}m`;
  return `${Math.floor(m / 60)}h${String(m % 60).padStart(2, '0')}`;
}

/** Legend row: [glyph node, label, title?] items. */
function vizLegend(items, cls) {
  return h('div', { class: ['viz-legend', cls] }, items.filter(Boolean).map(([glyph, label, title]) =>
    h('span', { class: 'lg-item', title }, glyph, h('span', null, label))));
}
function lgSvg(...children) {
  return svgEl('svg', { class: 'lg-glyph viz', width: 20, height: 14, viewBox: '0 0 20 14', 'aria-hidden': 'true' }, ...children);
}
/** Worker, regulator and disregulator shapes side by side; cls replaces the role fill (outcome fills). */
function lgRoles(cls) {
  return lgSvgWide(38, roleGlyph('worker', 6, 7, 3.8, cls), roleGlyph('regulator', 19, 7, 3.8, cls), roleGlyph('disregulator', 32, 7.6, 3.8, cls));
}
function swatch(cls, title) { return h('span', { class: ['lg-swatch', cls], title, 'aria-hidden': 'true' }); }

/** Red x mark (refusal, blocked call). */
function xMark(cx, cy, r) {
  return svgEl('path', { class: 'm-x', d: `M${r2(cx - r)},${r2(cy - r)}L${r2(cx + r)},${r2(cy + r)}M${r2(cx + r)},${r2(cy - r)}L${r2(cx - r)},${r2(cy + r)}` });
}
function diamond(cx, cy, r, cls) {
  return svgEl('path', { class: cls, d: `M${r2(cx)},${r2(cy - r)}L${r2(cx + r)},${r2(cy)}L${r2(cx)},${r2(cy + r)}L${r2(cx - r)},${r2(cy)}Z` });
}

/** Run a few promise jobs at a time. */
function jobQueue(limit) {
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

// =====================================================================================
// 11. View: timeline   #/run/<run>/timeline
// =====================================================================================

const TOOL_KINDS = ['bash', 'python', 'channel_post', 'channel_read', 'submit'];
const toolKind = fn => (TOOL_KINDS.includes(fn) ? fn : 'other');

/**
 * The verdict a chart draws for one agent. It counts only under the rule of /api/summary and the runs
 * table: a judge verdict on an episode with a transcript, in a run that did not fail. Any other verdict
 * is drawn as "not counted" and kept for the tooltip.
 * Returns {v: true|false|null (drawn outcome), raw (the judge's value), label, why}.
 */
function verdictInfo(detail, ep) {
  if (!detail) return { v: null, raw: null, label: 'unavailable', why: 'the run record could not be read' };
  const c = ep && ep.verdict ? ep.verdict.cheated : null;
  const raw = c === true || c === false ? c : null;
  if (raw == null) return { v: null, raw, label: 'not judged', why: null };
  if (!ep.has_transcript) return { v: null, raw, label: 'not counted', why: 'the episode has no transcript (harness crash or provider error)' };
  if (detail.failed) return { v: null, raw, label: 'not counted', why: 'the run failed (harness crash or provider error)' };
  return { v: raw, raw, label: raw ? 'cheated' : 'not cheated', why: null };
}
function verdictLine(info) {
  if (info.v != null) return `judge: ${info.label}`;
  if (info.raw != null) return `judge said ${info.raw ? 'cheated' : 'not cheated'}; not counted, since ${info.why}`;
  return info.why ? `judge: ${info.label} (${info.why})` : `judge: ${info.label}`;
}

/** Agents in lane order: grouped by role (disregulator, worker, regulator, then none), by name within a group. */
function laneOrder(tl) {
  const roles = tl.roles || {};
  const names = new Set(tl.agents || []);
  for (const e of tl.events || []) if (e.agent) names.add(e.agent);
  const rank = r => { const i = ROLE_ORDER.indexOf(r); return i < 0 ? ROLE_ORDER.length : i; };
  return [...names].sort((a, b) => rank(roles[a]) - rank(roles[b]) || a.localeCompare(b, 'en', { numeric: true }));
}

/**
 * Round-robin turn clock: each agent's k-th model call starts at turn k (counted from 1, as in the
 * tooltips and the raster), and time between two calls maps linearly between them. Each lane keeps
 * its own clock; the axis runs from 1 to maxTurns + 1.
 */
function turnClock(tl, agents) {
  const starts = new Map(agents.map(a => [a, []]));
  const ends = new Map(agents.map(a => [a, 0]));
  for (const e of tl.events || []) {
    if (!starts.has(e.agent) || e.t == null) continue;
    if (e.kind === 'model') starts.get(e.agent).push(e.t);
    const te = e.t_end != null ? e.t_end : e.t;
    if (te > ends.get(e.agent)) ends.set(e.agent, te);
  }
  const fns = new Map();
  let maxTurns = 0;
  for (const a of agents) {
    const st = starts.get(a).sort((x, y) => x - y);
    maxTurns = Math.max(maxTurns, st.length);
    const knots = st.map((t, i) => [t, i + 1]);
    knots.push([Math.max(ends.get(a), st.length ? st[st.length - 1] + 1e-3 : 0), st.length + 1]);
    fns.set(a, t => {
      if (!st.length || t <= knots[0][0]) return 1;
      let lo = 0;
      let hi = knots.length - 1;
      if (t >= knots[hi][0]) return knots[hi][1];
      while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (knots[mid][0] <= t) lo = mid; else hi = mid; }
      const [t0, k0] = knots[lo];
      const [t1, k1] = knots[hi];
      return k0 + (t1 > t0 ? (t - t0) / (t1 - t0) : 0) * (k1 - k0);
    });
  }
  return { at: (a, t) => (fns.has(a) ? fns.get(a)(t) : 1), maxTurns: Math.max(1, maxTurns) };
}

// Agent x turn raster: one cell per model call, coloured by the call's main action.
const RASTER_KINDS = [
  ['work', 'bash, python or another tool', 'sw-work'],
  ['post', 'channel_post', 'sw-post'],
  ['read', 'channel_read', 'sw-read'],
  ['touch', 'honeypot touch', 'sw-touch'],
  ['member', 'member read', 'sw-member'],
  ['submit', 'submit', 'sw-submit'],
  ['refusal', 'refusal sentence as output', 'sw-refusal'],
  ['none', 'no tool call', 'sw-none'],
];
const RASTER_LABEL = Object.fromEntries(RASTER_KINDS.map(([k, l]) => [k, l]));

/** Per agent: its model calls in order, each with the action of tool_calls[0] and the tool events that follow it. */
function rasterRows(tl, agents) {
  const by = new Map(agents.map(a => [a, { models: [], tools: [] }]));
  for (const e of tl.events || []) {
    const r = by.get(e.agent);
    if (!r || e.t == null) continue;
    if (e.kind === 'model') r.models.push(e);
    else if (e.kind === 'tool' || e.kind === 'submit') r.tools.push(e);
  }
  return agents.map(a => {
    const { models, tools } = by.get(a);
    models.sort((x, y) => x.t - y.t);
    tools.sort((x, y) => x.t - y.t);
    let j = 0;
    const cells = models.map((m, k) => {
      const next = k + 1 < models.length ? models[k + 1].t : Infinity;
      while (j < tools.length && tools[j].t < m.t) j++;
      const mine = [];
      while (j < tools.length && tools[j].t < next) mine.push(tools[j++]);
      const fn = (m.tool_calls || [])[0];
      let kind = !fn ? 'none' : fn === 'channel_post' ? 'post' : fn === 'channel_read' ? 'read' : fn === 'submit' ? 'submit' : 'work';
      if (m.refusal) kind = 'refusal';
      if (mine.some(x => x.member_read)) kind = 'member';
      else if (mine.some(x => x.honeypot_touch)) kind = 'touch';
      return { kind, fn, model: m, tools: mine };
    });
    return { agent: a, cells };
  });
}

function rasterColors() {
  return {
    work: cssVar('--raster-work', '#77776f'), post: cssVar('--tl-post', '#e87ba4'), read: cssVar('--tl-read', '#4a3aa7'),
    touch: cssVar('--hp-touch', '#2e94dc'), member: cssVar('--member', '#750459'), submit: cssVar('--text', '#1c1c1a'),
    refusal: cssVar('--tl-refusal', '#b02a20'), none: cssVar('--raster-none', '#e4e4de'),
  };
}

/** Canvas raster. thumb: tiny cells, no gaps, click opens the timeline. */
function rasterCanvas(tl, agents, opts = {}) {
  const { thumb = false, maxW = 900, onClick = null } = opts;
  const rows = rasterRows(tl, agents);
  const cols = Math.max(1, ...rows.map(r => r.cells.length));
  const cellH = thumb ? 4 : 13;
  const cw = thumb ? Math.max(1, Math.min(4, maxW / cols)) : Math.max(2, Math.min(12, maxW / cols));
  const W = Math.max(1, Math.ceil(cw * cols));
  const H = Math.max(1, rows.length * cellH);
  const dpr = window.devicePixelRatio || 1;
  const cv = h('canvas', { class: ['raster', thumb && 'raster-thumb'], width: Math.ceil(W * dpr), height: Math.ceil(H * dpr), role: 'img',
    'aria-label': `Agent by turn raster: ${plural(rows.length, 'agent')}, up to ${plural(cols, 'model call')} each` });
  cv.style.width = W + 'px';
  cv.style.height = H + 'px';
  const ctx = cv.getContext('2d');
  if (ctx) {
    ctx.scale(dpr, dpr);
    const col = rasterColors();
    const gx = cw >= 4 ? 1 : 0;
    rows.forEach((r, i) => r.cells.forEach((c, k) => {
      const x = k * cw;
      const y = i * cellH;
      const w = Math.max(0.8, cw - gx);
      const hh = cellH - 1;
      if (c.kind === 'refusal') {
        ctx.fillStyle = col.none; ctx.fillRect(x, y, w, hh);
        ctx.fillStyle = col.refusal; ctx.fillRect(x, y, w, Math.ceil(hh / 2));
      } else {
        ctx.fillStyle = col[c.kind] || col.none; ctx.fillRect(x, y, w, hh);
      }
    }));
  }
  const cellAt = ev => {
    const box = cv.getBoundingClientRect();
    const i = Math.floor((ev.clientY - box.top) / (box.height / rows.length));
    const k = Math.floor((ev.clientX - box.left) / (box.width / cols));
    const r = rows[i];
    return r && r.cells[k] ? { r, k, c: r.cells[k] } : (r ? { r, k, c: null } : null);
  };
  cv.addEventListener('pointermove', ev => {
    const hit = cellAt(ev);
    if (!hit) { tip.hide(); return; }
    const { r, k, c } = hit;
    const role = (tl.roles || {})[r.agent];
    if (!c) { tip.show(ev, [`${r.agent}${role ? ` (${role})` : ''}`, `${plural(r.cells.length, 'model call')}`, thumb ? 'click for the timeline' : null]); return; }
    const first = c.tools[0];
    tip.show(ev, [
      `${r.agent}${role ? ` (${role})` : ''} · call ${k + 1} of ${r.cells.length}`,
      `${RASTER_LABEL[c.kind]} · ${fmtSecs(c.model.t)}`,
      c.fn ? `tool calls: ${(c.model.tool_calls || []).join(', ')}` : 'no tool call',
      first && first.preview ? firstLine(first.preview, 160) : null,
      thumb ? 'click for the timeline' : null,
    ]);
  });
  cv.addEventListener('pointerleave', () => tip.hide());
  if (onClick) { cv.classList.add('clickable'); cv.addEventListener('click', onClick); }
  return { el: cv, rows, cols, cellH };
}

/** Raster legend; with `present` (a Set of kinds) only the kinds that occur. */
function rasterLegend(present) {
  return vizLegend(RASTER_KINDS.filter(([k]) => !present || present.has(k)).map(([, label, cls]) => [swatch(cls), label]), 'raster-legend');
}

/** Raster with agent labels, for the top of the timeline page. */
function rasterPanel(tl, agents, maxW) {
  const roles = tl.roles || {};
  const { el, rows, cols, cellH } = rasterCanvas(tl, agents, { maxW });
  const present = new Set(rows.flatMap(r => r.cells.map(c => c.kind)));
  return h('div', null,
    h('div', { class: 'raster-wrap' },
      h('div', { class: 'raster-labels', style: `grid-auto-rows: ${cellH}px` },
        agents.map(a => h('div', { class: 'raster-label' }, roles[a] ? roleIcon(roles[a], 10) : null, a))),
      h('div', { class: 'raster-scroll' }, el)),
    h('div', { class: 'muted small' }, `Columns are each agent’s own model calls, 1 to ${cols}; a call takes the action of its first tool call, and a honeypot touch or member read in the tool calls that follow it wins.`),
    rasterLegend(present));
}

/**
 * The three charts of one run's timeline (agent by turn raster, swimlanes, exposure cascade) as cards.
 * mount() draws them once the node is on screen and redraws on resize; unmount() removes the listener.
 * Verdicts come from the run record, not tl.verdicts: only the run record says whether an episode has
 * a transcript, and a verdict without one (or in a failed run) is not counted anywhere else either.
 */
function timelinePanels(run, tl, detail) {
  const agents = laneOrder(tl);
  const roles = tl.roles || {};
  const hasRoles = Object.keys(roles).length > 0;
  const eps = (detail && detail.episodes) || [];
  const epOf = a => eps.find(e => e.orbit_agent === a) || null;
  const infos = new Map(agents.map(a => [a, verdictInfo(detail, epOf(a))]));
  const verdictInfoOf = a => infos.get(a) || verdictInfo(detail, null);
  const verdictOf = a => verdictInfoOf(a).v;
  const countedVerdict = a => (verdictOf(a) != null ? epOf(a).verdict : null);
  const disclosedOf = a => { const v = countedVerdict(a); return !!(v && v.disclosed_access); };
  const touchOf = a => (tl.first_honeypot_touch || {})[a];
  const readOf = a => (tl.first_member_read || {})[a];
  // The touch heuristic against the judge: a touch where the counted verdict found no attempted access.
  const touchDoubtOf = a => { const v = countedVerdict(a); return touchOf(a) != null && !!v && v.attempted_access === false; };
  const ctx = { tl, agents, roles, hasRoles, verdictOf, verdictInfoOf, disclosedOf, touchOf, readOf, touchDoubtOf };
  const lane = buildLaneData(tl, agents);
  const notCounted = agents.filter(a => verdictInfoOf(a).raw != null && verdictInfoOf(a).v == null);
  const whyNot = [...new Set(notCounted.map(a => verdictInfoOf(a).why))].join('; ');

  const rasterHost = h('div', { class: 'viz-host' });
  const rasterCard = h('section', { class: 'card' }, h('h2', null, 'Agent by turn'), rasterHost);
  const swim = swimlaneCard(ctx, lane, run);
  const cascadeHost = h('div', { class: 'viz-host' });
  const cascadeCard = h('section', { class: 'card' },
    h('div', { class: 'sec-head' }, h('h2', null, 'Exposure cascade'), badge('exposure, not cause', 'warn', 'reading a post before a touch does not show the post caused it')),
    cascadeHost);
  const draw = () => {
    const w = rasterHost.clientWidth || 900;
    rasterHost.replaceChildren(rasterPanel(tl, agents, Math.max(200, w - 110)));
    swim.draw();
    cascadeHost.replaceChildren(cascadeView(ctx, cascadeHost.clientWidth || 900));
  };
  const onResize = debounce(() => { tip.hide(); draw(); }, 150);
  const note = h('div', null,
    muted(`${plural(agents.length, 'agent')} · ${plural((tl.events || []).length, 'event')} · ${plural((tl.board || []).length, 'board post')} · ${fmtSecs(tl.duration)} sample time. ` +
      'Honeypot touches and member reads are regex heuristics that place moments; the judge verdict is the record of truth.'),
    notCounted.length ? h('div', { class: 'muted small' },
      `${plural(notCounted.length, 'judge verdict')} drawn grey as “not counted”, as in the runs list and the summary: ${whyNot}.`) : null,
    !detail ? h('div', { class: 'muted small' }, 'The run record could not be read, so no verdict is drawn.') : null);
  return {
    node: h('div', null, rasterCard, swim.card, cascadeCard), note, draw,
    mount() { draw(); window.addEventListener('resize', onResize); },
    unmount() { window.removeEventListener('resize', onResize); tip.hide(); },
  };
}

async function viewTimeline({ run }, view) {
  let tl;
  let detail;
  try {
    [tl, detail] = await Promise.all([apiFresh(P.timeline(run)), apiFresh(P.run(run)).catch(() => null)]);
  } catch (e) {
    view.mounted(() => { document.title = `timeline · ${run} · results`; });
    return h('div', { class: 'page' },
      h('div', { class: 'page-head' }, h('h1', null, `${run}: timeline`),
        h('div', { class: 'links' }, link(R.run(run), 'run page', 'btn btn-small'), link(R.swarm(), 'swarm visualizations', 'btn btn-small'))),
      h('p', { class: 'muted' }, 'The timeline is read from the run’s eval log. A run that is still being produced has no log yet; press Reload once it has finished.'),
      errorBox(e, 'timeline'));
  }
  const eps = (detail && detail.episodes) || [];
  const rj = (detail && detail.run_json) || {};
  const sw = rj.swarm || null;
  const logOk = !detail || !!detail.eval_log_exists;
  const panels = timelinePanels(run, tl, detail);
  const head = h('div', { class: 'page-head' },
    h('h1', null, `${run}: timeline`),
    h('div', { class: 'chips' },
      detail && detail.failed ? badge('failed run', 'bad') : null,
      badge(detail ? settingOf(detail).label : '?', 'plain'),
      badge(`control: ${(detail && detail.control) || 'baseline'}`, 'def'),
      sw ? badge(`composition ${sw.composition}`, 'plain', `${sw.regulators} regulators, ${sw.disregulators} disregulators, ${sw.workers} workers; seed ${sw.seed}`) : null,
      tl.limit ? badge(`limit: ${tl.limit}`, 'warn', 'the sample ended on this limit') : null),
    h('div', { class: 'links' },
      link(R.run(run), 'run page', 'btn btn-small'),
      eps.filter(e => e.has_transcript).map(e => link(R.episode(run, e.episode), `transcript ${e.orbit_agent || e.episode}`, 'btn btn-small')),
      logLink(logOk, detail && detail.eval_log, R.context(run), 'context windows', 'btn btn-small')),
    panels.note);
  view.mounted(() => { document.title = `timeline · ${run} · results`; panels.mount(); });
  view.cleanup(() => panels.unmount());
  return h('div', { class: 'page' }, head, panels.node);
}

/** Events split per lane, posts, and de-duplicated reads (a read and a model exposure at the same moment draw once). */
function buildLaneData(tl, agents) {
  const lanes = new Map(agents.map(a => [a, { models: [], defcalls: [], tools: [], blocks: [] }]));
  const events = (tl.events || []).filter(e => e.t != null);
  const freeBlocks = [];
  for (const e of events) {
    const L = lanes.get(e.agent);
    if (e.kind === 'defense') { if (e.allow === false) (L ? L.blocks : freeBlocks).push(e); continue; }
    if (!L) continue;
    if (e.kind === 'model') L.models.push(e);
    else if (e.kind === 'defense_call') L.defcalls.push(e);
    else if (e.kind === 'tool' || e.kind === 'submit') L.tools.push(e);
  }
  // A block's band starts where the control call that decided it started (same boundary preferred,
  // then the latest end): a control call that ended in (b.t - 2, b.t + 0.05]. The calls are sorted by
  // end once per lane and the window is found by binary search.
  const blockFrom = new Map();
  for (const L of lanes.values()) {
    if (!L.blocks.length) continue;
    const dcs = L.defcalls.filter(dc => dc.t_end != null).sort((x, y) => x.t_end - y.t_end);
    for (const b of L.blocks) {
      let lo = 0;
      let hi = dcs.length;
      while (lo < hi) { const mid = (lo + hi) >> 1; if (dcs[mid].t_end <= b.t + 0.05) lo = mid + 1; else hi = mid; }
      let first = lo;
      while (first > 0 && b.t - dcs[first - 1].t_end < 2) first--;
      const cands = dcs.slice(first, lo);
      const want = `${b.defense}:${b.boundary}`;
      cands.sort((x, y) => (y.defense === want) - (x.defense === want) || y.t_end - x.t_end);
      blockFrom.set(b, cands.length ? cands[0].t : b.t);
    }
  }
  const posts = (tl.board || []).filter(p => p.t != null && p.sender);
  const reads = [];
  for (const p of posts) {
    const by = new Map();
    for (const r of p.read_by || []) {
      if (!r.agent || r.agent === p.sender || r.t == null || !lanes.has(r.agent)) continue;
      const key = `${r.agent}|${r.t}`;
      const prev = by.get(key);
      if (!prev || (prev.how !== 'read' && r.how === 'read')) by.set(key, { post: p, agent: r.agent, t: r.t, how: r.how });
    }
    reads.push(...by.values());
  }
  let maxTok = 1;
  let duration = 0;
  for (const e of events) {
    if (e.kind === 'model') maxTok = Math.max(maxTok, (e.tokens_in || 0) + (e.tokens_out || 0));
    duration = Math.max(duration, e.t_end != null ? e.t_end : e.t);
  }
  duration = Math.max(duration, tl.duration || 0) || 1;
  return { lanes, blockFrom, freeBlocks, posts, reads, maxTok, duration, clock: turnClock(tl, agents) };
}

function swimlaneCard(ctx, lane, run) {
  const { agents, roles, hasRoles, verdictInfoOf, touchOf, readOf, touchDoubtOf } = ctx;
  const { lanes, blockFrom, freeBlocks, posts, reads, maxTok, duration, clock } = lane;
  const L_ = [...lanes.values()];
  const anyDefcalls = L_.some(L => L.defcalls.length);
  // What occurs in this run, so the legend lists only marks that can be on screen.
  const allTools = L_.flatMap(L => L.tools);
  const kinds = new Set(allTools.map(e => toolKind(e.function)));
  const anyTouch = allTools.some(e => e.honeypot_touch);
  const anyMember = allTools.some(e => e.member_read);
  const anyBlockedTool = allTools.some(e => e.blocked);
  const anyRefusal = L_.some(L => L.models.some(m => m.refusal));
  const anyBlocks = freeBlocks.length > 0 || L_.some(L => L.blocks.length);
  const anyRead = reads.some(r => r.how === 'read');
  const anyExposure = reads.some(r => r.how !== 'read');
  const st = { mode: 'time', lo: 0, hi: duration, arrows: true, defcalls: true };
  const full = () => (st.mode === 'time' ? [0, duration] : [1, clock.maxTurns + 1]);
  const X = (a, t) => (st.mode === 'time' ? t : clock.at(a, t));
  const host = h('div', { class: 'viz-host swim-host' });

  // Controls: axis mode, layers, zoom.
  const modeBtns = h('div', { class: 'seg', role: 'group', 'aria-label': 'x axis' },
    [['time', 'seconds from start'], ['turn', 'turn index']].map(([v, label]) => {
      const b = h('button', { type: 'button', class: ['seg-btn', st.mode === v && 'active'], 'aria-pressed': st.mode === v ? 'true' : 'false', dataset: { v } }, label);
      b.addEventListener('click', () => {
        if (st.mode === v) return;
        st.mode = v;
        for (const x of modeBtns.children) { x.classList.toggle('active', x.dataset.v === v); x.setAttribute('aria-pressed', x.dataset.v === v ? 'true' : 'false'); }
        [st.lo, st.hi] = full();
        syncZoom();
        draw();
      });
      return b;
    }));
  const chk = (label, key, title) => {
    const c = h('input', { type: 'checkbox', checked: st[key] });
    c.addEventListener('change', () => { st[key] = c.checked; draw(); });
    return h('label', { class: 'field check', title }, c, h('span', null, label));
  };
  const rFrom = h('input', { type: 'range', min: 0, max: 1000, step: 1, value: 0, class: 'zoom-range', 'aria-label': 'zoom from' });
  const rTo = h('input', { type: 'range', min: 0, max: 1000, step: 1, value: 1000, class: 'zoom-range', 'aria-label': 'zoom to' });
  const zoomText = h('span', { class: 'muted small zoom-text' });
  const fmtX = v => (st.mode === 'time' ? fmtSecs(v) : `call ${+v.toFixed(1)}`);
  function syncZoom() {
    const [f0, f1] = full();
    rFrom.value = Math.round(((st.lo - f0) / (f1 - f0)) * 1000);
    rTo.value = Math.round(((st.hi - f0) / (f1 - f0)) * 1000);
    zoomText.textContent = `${fmtX(st.lo)} to ${fmtX(st.hi)}`;
  }
  let raf = 0;
  const drawSoon = () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; draw(); }); };
  const onRange = which => {
    let a = +rFrom.value;
    let b = +rTo.value;
    if (b - a < 2) { if (which === 'from') a = b - 2; else b = a + 2; }
    a = Math.max(0, a); b = Math.min(1000, b);
    rFrom.value = a; rTo.value = b;
    const [f0, f1] = full();
    st.lo = f0 + ((f1 - f0) * a) / 1000;
    st.hi = f0 + ((f1 - f0) * b) / 1000;
    zoomText.textContent = `${fmtX(st.lo)} to ${fmtX(st.hi)}`;
    drawSoon();
  };
  rFrom.addEventListener('input', () => onRange('from'));
  rTo.addEventListener('input', () => onRange('to'));
  function setRange(lo, hi) {
    const [f0, f1] = full();
    const minSpan = (f1 - f0) / 500;
    lo = Math.max(f0, Math.min(lo, hi));
    hi = Math.min(f1, Math.max(lo, hi));
    if (hi - lo < minSpan) { const c = (lo + hi) / 2; lo = Math.max(f0, c - minSpan / 2); hi = Math.min(f1, lo + minSpan); }
    st.lo = lo; st.hi = hi;
    syncZoom();
    draw();
  }
  const resetBtn = h('button', { type: 'button', class: 'btn btn-small', onclick: () => setRange(...full()) }, 'reset zoom');

  const controls = h('div', { class: 'filters viz-controls' },
    h('span', { class: 'field' }, h('span', { class: 'field-label' }, 'x axis'), modeBtns),
    posts.length ? chk('board arrows', 'arrows', 'arrows from each post to its readers') : null,
    anyDefcalls ? chk('control model calls', 'defcalls', 'the control’s own judge calls, as a strip at the top of the lane') : null,
    h('span', { class: 'zoom' }, h('span', { class: 'field-label' }, 'zoom'), rFrom, rTo, zoomText, resetBtn),
    muted('drag across the chart to zoom; double-click to reset', 'small'));

  const legend = vizLegend([
    [lgSvg(svgEl('rect', { class: 'm-model', x: 6, y: 4, width: 8, height: 9, rx: 1.5 })), 'model call (height: tokens in + out, log)'],
    anyDefcalls ? [lgSvg(svgEl('rect', { class: 'm-defcall', x: 2, y: 5, width: 16, height: 3 })), 'control’s model call'] : null,
    kinds.has('bash') ? [lgSvg(svgEl('line', { class: 'tk tk-bash', x1: 10, x2: 10, y1: 2, y2: 13 })), 'bash'] : null,
    kinds.has('python') ? [lgSvg(svgEl('line', { class: 'tk tk-python', x1: 10, x2: 10, y1: 3, y2: 13 }), svgEl('rect', { class: 'fill-python', x: 8, y: 1, width: 4, height: 4 })), 'python'] : null,
    kinds.has('channel_post') ? [lgSvg(svgEl('line', { class: 'tk tk-channel_post', x1: 10, x2: 10, y1: 1, y2: 7 })), 'channel_post (upper half)'] : null,
    kinds.has('channel_read') ? [lgSvg(svgEl('line', { class: 'tk tk-channel_read', x1: 10, x2: 10, y1: 7, y2: 13 })), 'channel_read (lower half)'] : null,
    kinds.has('submit') ? [lgSvg(svgEl('line', { class: 'tk tk-submit', x1: 7, x2: 7, y1: 1, y2: 13 }), svgEl('path', { class: 'fill-ink', d: 'M7,1 l7,3 l-7,3 z' })), 'submit'] : null,
    kinds.has('other') ? [lgSvg(svgEl('line', { class: 'tk tk-other', x1: 10, x2: 10, y1: 2, y2: 13 })), 'another tool'] : null,
    posts.length ? [lgSvg(svgEl('circle', { class: 'm-post', cx: 10, cy: 7, r: 4.5 })), 'board post'] : null,
    anyRead ? [lgSvg(svgEl('line', { class: 'arrow', x1: 1, y1: 7, x2: 19, y2: 7 })), 'post read'] : null,
    anyExposure ? [lgSvg(svgEl('line', { class: 'arrow exposure', x1: 1, y1: 7, x2: 19, y2: 7 })), 'post shown to the model only'] : null,
    anyTouch ? [lgSvg(svgEl('circle', { class: 'm-touch', cx: 10, cy: 7, r: 5 })), 'honeypot touch'] : null,
    anyMember ? [lgSvg(diamond(10, 7, 5, 'm-member')), 'member read'] : null,
    anyBlocks ? [lgSvg(svgEl('rect', { class: 'm-block', x: 5, y: 0, width: 9, height: 14 }), svgEl('line', { class: 'm-block-line', x1: 14, x2: 14, y1: 0, y2: 14 })), 'defense block (band from the control call to the decision)'] : null,
    anyRefusal || anyBlockedTool ? [lgSvg(xMark(10, 7, 4)),
      anyRefusal && anyBlockedTool ? 'refusal sentence or blocked tool call' : anyRefusal ? 'refusal sentence as output' : 'blocked tool call'] : null,
    hasRoles ? [lgRoles(), 'roles: worker, regulator, disregulator'] : null,
  ]);

  const card = h('section', { class: 'card' },
    h('h2', null, 'Agent swimlanes'),
    controls, host, legend,
    posts.length < 1 ? null : h('div', { class: 'muted small' },
      'In turn-index mode each lane runs on its own clock (its k-th model call sits at k), so an arrow between lanes can point backwards.'));

  function draw() {
    tip.hide();
    const W = Math.max(640, host.clientWidth || 1000);
    const n = Math.max(1, agents.length);
    const laneH = n <= 1 ? 76 : n <= 2 ? 64 : n <= 4 ? 52 : n <= 8 ? 42 : 36;
    const padL = 132;
    const padR = 118;
    const axisH = 26;
    const groupH = hasRoles ? 18 : 0;
    const laneY = new Map();
    const groups = [];
    let y = axisH + 2;
    let cur;
    for (const a of agents) {
      const role = roles[a] || null;
      if (hasRoles && role !== cur) { groups.push({ role, y, n: agents.filter(x => (roles[x] || null) === role).length }); y += groupH; cur = role; }
      laneY.set(a, y);
      y += laneH;
    }
    const H = y + 4;
    const x0 = padL;
    const x1 = W - padR;
    const { lo, hi } = st;
    const sx = v => x0 + ((v - lo) / (hi - lo || 1)) * (x1 - x0);
    const inv = px => lo + ((px - x0) / (x1 - x0)) * (hi - lo);
    const vis = (xa, xb) => xb >= x0 - 8 && xa <= x1 + 8;
    const uid = ++vizUid;
    const hits = [];

    const svg = svgEl('svg', { class: 'viz timeline-svg', width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: 'img',
      'aria-label': `Swimlane timeline of ${run}: ${agents.length} agents` });
    svg.appendChild(svgEl('defs', null,
      svgEl('clipPath', { id: `tlclip-${uid}` }, svgEl('rect', { x: x0, y: 0, width: x1 - x0, height: H })),
      svgEl('marker', { id: `tlarrow-${uid}`, viewBox: '0 0 8 8', refX: 7.5, refY: 4, markerWidth: 6, markerHeight: 6, orient: 'auto' },
        svgEl('path', { class: 'arrowhead', d: 'M0,0L8,4L0,8Z' }))));

    agents.forEach((a, i) => svg.appendChild(svgEl('rect', { class: ['lane-bg', i % 2 && 'alt'], x: 0, y: laneY.get(a), width: W, height: laneH })));
    for (const g of groups) {
      svg.appendChild(svgEl('line', { class: 'grp-rule', x1: 0, x2: W, y1: g.y + 0.5, y2: g.y + 0.5 }));
      svg.appendChild(svgEl('text', { class: 'grp-label', x: 8, y: g.y + 13 }, `${(ROLE_PLURAL[g.role] || 'no role').toUpperCase()} (${g.n})`));
    }

    // Axis and gridlines.
    const ticks = ticksFor(lo, hi, Math.max(3, Math.floor((x1 - x0) / 80)), st.mode === 'time' ? TIME_STEPS : COUNT_STEPS);
    for (const v of ticks) {
      const px = sx(v);
      svg.appendChild(svgEl('line', { class: 'grid', x1: px, x2: px, y1: axisH - 4, y2: H - 2 }));
      svg.appendChild(svgEl('text', { class: 'tick-label', x: px, y: axisH - 9, 'text-anchor': 'middle' }, st.mode === 'time' ? fmtTick(v) : String(v)));
    }
    svg.appendChild(svgEl('text', { class: 'axis-title', x: 10, y: axisH - 9 }, st.mode === 'time' ? 'seconds from start' : 'agent’s model call'));

    const marks = svgEl('g', { 'clip-path': `url(#tlclip-${uid})` });
    const arrowsG = svgEl('g', { 'clip-path': `url(#tlclip-${uid})` });
    const over = svgEl('g', { 'clip-path': `url(#tlclip-${uid})` });
    const barMax = laneH * 0.52;
    const barH = tok => 3 + (barMax - 3) * (tok > 0 ? Math.log1p(tok) / Math.log1p(maxTok) : 0);

    for (const a of agents) {
      const L = lanes.get(a);
      const ly = laneY.get(a);
      const base = ly + laneH - 4;
      const top = ly + 7;
      const mid = ly + laneH / 2;
      for (const b of L.blocks) {
        const xa = sx(X(a, blockFrom.get(b)));
        const xb = sx(X(a, b.t));
        if (!vis(xa, xb)) continue;
        const w = Math.max(4, xb - xa);
        marks.appendChild(svgEl('rect', { class: 'm-block', x: xb - w, y: ly, width: w, height: laneH }));
        marks.appendChild(svgEl('line', { class: 'm-block-line', x1: xb, x2: xb, y1: ly, y2: ly + laneH }));
        hits.push({ x0: xb - w, x1: xb, y0: ly, y1: ly + laneH, pri: 1,
          lines: () => [`BLOCKED by ${b.defense || '?'} at ${b.boundary || '?'}`, `${a} · ${fmtSecs(b.t)}`, b.tool ? `tool: ${b.tool}` : null, b.reason ? firstLine(b.reason, 220) : null] });
      }
      if (st.defcalls) {
        for (const dc of L.defcalls) {
          const xa = sx(X(a, dc.t));
          const xb = sx(X(a, dc.t_end != null ? dc.t_end : dc.t));
          if (!vis(xa, xb)) continue;
          marks.appendChild(svgEl('rect', { class: 'm-defcall', x: xa, y: ly + 1.5, width: Math.max(1, xb - xa), height: 3 }));
          hits.push({ x0: xa, x1: Math.max(xa + 1, xb), y0: ly, y1: ly + 5, pri: 0,
            lines: () => [`control call: ${dc.defense || '?'}`, `${a} · ${fmtSecs(dc.t)} for ${fmtSecs((dc.t_end || dc.t) - dc.t)}`, `tokens in ${fmtInt(dc.tokens_in)} · out ${fmtInt(dc.tokens_out)}`] });
        }
      }
      L.models.forEach((m, k) => {
        const xa = sx(X(a, m.t));
        const xb = sx(X(a, m.t_end != null ? m.t_end : m.t));
        if (!vis(xa, xb)) return;
        const tok = (m.tokens_in || 0) + (m.tokens_out || 0);
        const bh = barH(tok);
        const w = Math.max(1.5, xb - xa);
        marks.appendChild(svgEl('rect', { class: ['m-model', m.error && 'err'], x: xa, y: base - bh, width: w, height: bh, rx: Math.min(1.5, w / 2) }));
        if (m.refusal) over.appendChild(xMark(xa + w / 2, base - bh - 5, 3.2));
        hits.push({ x0: xa, x1: xa + w, y0: base - bh - (m.refusal ? 9 : 2), y1: base, pri: m.refusal ? 3 : 1,
          lines: () => [`${a} · model call ${k + 1} of ${L.models.length}`,
            `${fmtSecs(m.t)} for ${fmtSecs((m.t_end || m.t) - m.t)}`,
            m.tokens_in == null && m.tokens_out == null ? 'no token usage recorded' : `tokens in ${fmtInt(m.tokens_in)} · out ${fmtInt(m.tokens_out)}`,
            `tool calls: ${(m.tool_calls || []).join(', ') || 'none'}`,
            m.refusal ? 'REFUSAL: the output text is the defense refusal sentence' : null,
            m.output_blocked ? 'a defense replaced this completion (block event)' : null, m.error ? 'model error' : null] });
      });
      for (const e of L.tools) {
        const fn = toolKind(e.function);
        const xa = sx(X(a, e.t));
        const xe = e.t_end != null ? sx(X(a, e.t_end)) : xa;
        if (!vis(xa, xe)) continue;
        let ya = top + laneH * 0.08;
        let yb = base;
        if (fn === 'channel_post') { ya = top - 3; yb = mid; } else if (fn === 'channel_read') { ya = mid; yb = base; } else if (fn === 'submit') { ya = top - 4; }
        if (xe - xa > 3) marks.appendChild(svgEl('line', { class: ['dur', 'tk-' + fn], x1: xa, x2: xe, y1: (ya + yb) / 2, y2: (ya + yb) / 2 }));
        marks.appendChild(svgEl('line', { class: ['tk', 'tk-' + fn], x1: xa, x2: xa, y1: ya, y2: yb }));
        if (fn === 'python') marks.appendChild(svgEl('rect', { class: 'fill-python', x: xa - 2, y: ya - 3, width: 4, height: 4 }));
        if (fn === 'submit') marks.appendChild(svgEl('path', { class: 'fill-ink', d: `M${r2(xa)},${r2(ya)}l7,3l-7,3z` }));
        if (e.blocked) over.appendChild(xMark(xa, ya - 5, 3.2));
        if (e.honeypot_touch) over.appendChild(svgEl('circle', { class: 'm-touch', cx: xa, cy: mid, r: 5.5 }));
        if (e.member_read) over.appendChild(diamond(xa, mid, 4.6, 'm-member'));
        const pri = e.member_read || e.honeypot_touch ? 4 : e.blocked ? 3 : 2;
        hits.push({ x0: xa - 1, x1: Math.max(xa + 1, xe), y0: ya - (e.blocked ? 9 : 2), y1: yb, pri,
          lines: () => [`${a} · ${e.function}${e.blocked ? ' · BLOCKED' : e.error ? ' · error' : ''}`,
            `${fmtSecs(e.t)}${e.t_end != null ? ` for ${fmtSecs(e.t_end - e.t)}` : ''}`,
            e.honeypot_touch ? 'honeypot touch: the call names another agent’s branch, commit or archive' : null,
            e.member_read ? 'member read: the result carries text found only in agent-014’s archive' : null,
            e.preview ? String(e.preview).slice(0, 300) : null] });
      }
      // Verdict at the lane's end: only a counted verdict is drawn red or green.
      const vi = verdictInfoOf(a);
      const v = vi.v;
      const vx = x1 + 16;
      const cy = ly + laneH / 2 - (laneH >= 36 && touchOf(a) != null ? 5 : 0);
      if (v === true) {
        svg.appendChild(svgEl('rect', { class: 'v-cheated', x: vx - 5, y: cy - 5, width: 10, height: 10, rx: 2 }));
        svg.appendChild(svgEl('path', { class: 'v-mark', d: `M${vx - 2.5},${cy - 2.5}L${vx + 2.5},${cy + 2.5}M${vx + 2.5},${cy - 2.5}L${vx - 2.5},${cy + 2.5}` }));
      } else if (v === false) {
        svg.appendChild(svgEl('circle', { class: 'v-honest', cx: vx, cy, r: 5.5 }));
        svg.appendChild(svgEl('path', { class: 'v-mark', d: `M${vx - 2.8},${cy}L${vx - 0.6},${cy + 2.4}L${vx + 3},${cy - 2.4}` }));
      } else {
        svg.appendChild(svgEl('circle', { class: 'v-unknown', cx: vx, cy, r: 4.5 }));
      }
      svg.appendChild(svgEl('text', { class: ['verdict-label', v == null && 't-muted'], x: vx + 10, y: cy + 4 }, vi.label));
      if (laneH >= 36 && touchOf(a) != null) svg.appendChild(svgEl('text', { class: 't-muted t-small', x: vx + 10, y: cy + 17 }, `touch ${fmtSecs(touchOf(a))}`));
      hits.push({ x0: x1 + 4, x1: W, y0: ly, y1: ly + laneH, pri: 0, lines: () => laneInfo(a) });
      // Lane label.
      const role = roles[a];
      const lcy = ly + laneH / 2;
      if (role) svg.appendChild(roleGlyph(role, 16, lcy, 5.5));
      const name = a.length > 16 ? a.slice(0, 15) + '…' : a;
      svg.appendChild(svgEl('text', { class: 'lane-name', x: role ? 28 : 10, y: lcy + (role && laneH >= 36 ? -1 : 4) }, name));
      if (role && laneH >= 36) svg.appendChild(svgEl('text', { class: 't-muted t-small', x: 28, y: lcy + 12 }, role));
      hits.push({ x0: 0, x1: x0 - 4, y0: ly, y1: ly + laneH, pri: 0, lines: () => laneInfo(a) });
    }
    // A block with no owning lane has no turn clock to map it, so it is drawn on the seconds axis only.
    for (const b of st.mode === 'time' ? freeBlocks : []) {
      const xb = sx(b.t);
      if (!vis(xb, xb)) continue;
      marks.appendChild(svgEl('line', { class: 'm-block-line', x1: xb, x2: xb, y1: axisH, y2: H }));
    }

    // Board: arrows from the post on the sender's lane to each reader's lane at read time.
    if (st.arrows) {
      for (const r of reads) {
        const p = r.post;
        if (!laneY.has(p.sender)) continue;
        const xa = sx(X(p.sender, p.t));
        const xb = sx(X(r.agent, r.t));
        if ((xa < x0 && xb < x0) || (xa > x1 && xb > x1)) continue;
        const ya = laneY.get(p.sender) + laneH * 0.3;
        const yb = laneY.get(r.agent) + laneH * 0.3;
        arrowsG.appendChild(svgEl('line', { class: ['arrow', r.how !== 'read' && 'exposure'], x1: xa, y1: ya, x2: xb, y2: yb, 'marker-end': `url(#tlarrow-${uid})` }));
        hits.push({ x0: xb - 4, x1: xb + 2, y0: yb - 4, y1: yb + 4, pri: 3,
          lines: () => [`${r.agent} ${r.how === 'read' ? 'read' : 'was shown'} post #${p.sequence} from ${p.sender}`,
            `${fmtSecs(r.t)}, posted at ${fmtSecs(p.t)}`, String(p.content || '').slice(0, 400)] });
      }
    }
    for (const p of posts) {
      if (!laneY.has(p.sender)) continue;
      const px = sx(X(p.sender, p.t));
      if (!vis(px, px)) continue;
      const py = laneY.get(p.sender) + laneH * 0.3;
      over.appendChild(svgEl('circle', { class: 'm-post', cx: px, cy: py, r: 4.5 }));
      const readers = [...new Set((p.read_by || []).filter(x => x.agent && x.agent !== p.sender).map(x => x.agent))];
      hits.push({ x0: px - 5, x1: px + 5, y0: py - 5, y1: py + 5, pri: 5,
        lines: () => [`post #${p.sequence} by ${p.sender} at ${fmtSecs(p.t)}`, readers.length ? `read by ${readers.join(', ')}` : 'read by no other agent',
          String(p.content || '').slice(0, 400)] });
    }
    svg.append(marks, arrowsG, over);

    // Crosshair, brush, tooltip.
    const cross = svgEl('line', { class: 'crosshair', x1: 0, x2: 0, y1: axisH - 4, y2: H, visibility: 'hidden' });
    const crossLabel = svgEl('text', { class: 'cross-label', x: 0, y: 10, 'text-anchor': 'middle', visibility: 'hidden' });
    const brush = svgEl('rect', { class: 'brush', x: 0, y: axisH - 4, width: 0, height: H - axisH + 4, visibility: 'hidden' });
    svg.append(brush, cross, crossLabel);
    const pt = ev => { const b = svg.getBoundingClientRect(); return [(ev.clientX - b.left) * (W / b.width), (ev.clientY - b.top) * (H / b.height)]; };
    const pick = (px, py) => {
      let best = null;
      let bd = 6;
      for (const it of hits) {
        const d = Math.max(0, it.x0 - px, px - it.x1) + Math.max(0, it.y0 - py, py - it.y1) - it.pri * 0.4;
        if (d < bd) { bd = d; best = it; }
      }
      return best;
    };
    let drag = null;
    svg.addEventListener('pointerdown', ev => {
      const [px, py] = pt(ev);
      if (ev.button !== 0 || px < x0 || px > x1 || py < axisH - 4) return;
      drag = { x: px, id: ev.pointerId };
      svg.setPointerCapture(ev.pointerId);
    });
    svg.addEventListener('pointermove', ev => {
      const [px, py] = pt(ev);
      const inPlot = px >= x0 && px <= x1;
      cross.setAttribute('visibility', inPlot ? 'visible' : 'hidden');
      crossLabel.setAttribute('visibility', inPlot ? 'visible' : 'hidden');
      if (inPlot) {
        cross.setAttribute('x1', r2(px)); cross.setAttribute('x2', r2(px));
        crossLabel.setAttribute('x', r2(Math.min(x1 - 24, Math.max(x0 + 24, px))));
        crossLabel.textContent = st.mode === 'time' ? fmtSecs(inv(px)) : `call ${+inv(px).toFixed(1)}`;
      }
      if (drag) {
        const a = Math.max(x0, Math.min(drag.x, px));
        const b = Math.min(x1, Math.max(drag.x, px));
        brush.setAttribute('x', r2(a)); brush.setAttribute('width', r2(b - a));
        brush.setAttribute('visibility', b - a > 2 ? 'visible' : 'hidden');
        tip.hide();
        return;
      }
      const it = pick(px, py);
      if (it) tip.show(ev, it.lines()); else tip.hide();
    });
    svg.addEventListener('pointerup', ev => {
      if (!drag) return;
      const [px] = pt(ev);
      const a = Math.max(x0, Math.min(drag.x, px));
      const b = Math.min(x1, Math.max(drag.x, px));
      drag = null;
      brush.setAttribute('visibility', 'hidden');
      if (b - a > 6) setRange(inv(a), inv(b));
    });
    // A cancelled touch or pen drag ends the brush without zooming.
    svg.addEventListener('pointercancel', () => {
      drag = null;
      brush.setAttribute('visibility', 'hidden');
    });
    svg.addEventListener('pointerleave', () => {
      tip.hide();
      cross.setAttribute('visibility', 'hidden');
      crossLabel.setAttribute('visibility', 'hidden');
    });
    svg.addEventListener('dblclick', () => setRange(...full()));
    host.replaceChildren(svg);
  }

  function laneInfo(a) {
    const L = lanes.get(a);
    const blocks = L.blocks.length;
    const refusals = L.models.filter(m => m.refusal).length;
    return [`${a}${roles[a] ? ` (${roles[a]})` : ''}`,
      verdictLine(verdictInfoOf(a)),
      `${plural(L.models.length, 'model call')} · ${plural(L.tools.length, 'tool call')}`,
      touchOf(a) != null ? `first honeypot touch ${fmtSecs(touchOf(a))}` : 'no honeypot touch',
      touchDoubtOf(a) ? 'the judge found no attempted access, so the touch heuristic may be a false positive' : null,
      readOf(a) != null ? `first member read ${fmtSecs(readOf(a))}` : null,
      blocks || refusals ? `${plural(blocks, 'block')}, ${plural(refusals, 'refusal')}` : null];
  }

  syncZoom();
  return { card, draw };
}

/**
 * Edges A -> B: posts of A that B read (or was shown) before B's first honeypot touch. An edge needs a
 * read before a touch, so an agent that never touched the honeypot has no incoming edge.
 */
function exposureEdges(tl) {
  const touch = tl.first_honeypot_touch || {};
  const edges = new Map();
  for (const p of tl.board || []) {
    if (!p.sender) continue;
    const seen = new Set();
    for (const r of p.read_by || []) {
      const B = r.agent;
      if (!B || B === p.sender || r.t == null || seen.has(B)) continue;
      const cutoff = touch[B];
      if (cutoff == null || !(r.t < cutoff)) continue;
      seen.add(B);
      const k = `${p.sender}\u0000${B}`;
      if (!edges.has(k)) edges.set(k, { from: p.sender, to: B, posts: [] });
      edges.get(k).posts.push(p);
    }
  }
  return [...edges.values()];
}

function cascadeView(ctx, width) {
  const { tl, agents, roles, hasRoles, verdictInfoOf, disclosedOf, touchOf, readOf, touchDoubtOf } = ctx;
  const edges = exposureEdges(tl).filter(e => agents.includes(e.from) && agents.includes(e.to));
  const W = Math.max(560, width);
  const rowH = agents.length <= 4 ? 44 : 32;
  const padL = 132;
  const padT = 26;
  const neverX = W - 64;
  const divX = W - 118;
  const x0 = padL + 24;
  const x1 = divX - 40;
  const H = padT + agents.length * rowH + 30;
  const touched = agents.filter(a => touchOf(a) != null);
  const tMax = Math.max(1e-6, ...touched.map(a => touchOf(a)));
  const sx = t => (touched.length === 1 ? (x0 + x1) / 2 : x0 + (t / tMax) * (x1 - x0));
  const pos = new Map(agents.map((a, i) => [a, [touchOf(a) != null ? sx(touchOf(a)) : neverX, padT + i * rowH + rowH / 2]]));
  const uid = ++vizUid;
  const nodeR = 8;

  const svg = svgEl('svg', { class: 'viz cascade-svg', width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: 'img',
    'aria-label': `Exposure cascade: ${plural(edges.length, 'edge')} between ${plural(agents.length, 'agent')}` });
  svg.appendChild(svgEl('defs', null, svgEl('marker', { id: `csarrow-${uid}`, viewBox: '0 0 8 8', refX: 7, refY: 4, markerWidth: 5, markerHeight: 5, orient: 'auto' },
    svgEl('path', { class: 'arrowhead', d: 'M0,0L8,4L0,8Z' }))));
  agents.forEach((a, i) => svg.appendChild(svgEl('rect', { class: ['lane-bg', i % 2 && 'alt'], x: 0, y: padT + i * rowH, width: W, height: rowH })));
  svg.appendChild(svgEl('line', { class: 'grp-rule dashed', x1: divX, x2: divX, y1: padT - 18, y2: H - 22 }));
  svg.appendChild(svgEl('text', { class: 'axis-title', x: (divX + W) / 2, y: 14, 'text-anchor': 'middle' }, 'never touched'));
  svg.appendChild(svgEl('text', { class: 'axis-title', x: x0, y: 14 }, 'first honeypot touch →'));
  const ticks = touched.length ? ticksFor(0, tMax, Math.max(2, Math.floor((x1 - x0) / 90)), TIME_STEPS) : [];
  if (touched.length > 1) {
    for (const v of ticks) {
      svg.appendChild(svgEl('line', { class: 'grid', x1: sx(v), x2: sx(v), y1: padT, y2: H - 22 }));
      svg.appendChild(svgEl('text', { class: 'tick-label', x: sx(v), y: H - 8, 'text-anchor': 'middle' }, fmtTick(v)));
    }
  } else if (touched.length === 1) {
    svg.appendChild(svgEl('text', { class: 'tick-label', x: sx(0), y: H - 8, 'text-anchor': 'middle' }, fmtTick(touchOf(touched[0]))));
  }
  agents.forEach((a, i) => {
    const cy = padT + i * rowH + rowH / 2;
    svg.appendChild(svgEl('text', { class: 'lane-name', x: 10, y: cy + (hasRoles && rowH >= 32 ? -1 : 4) }, a.length > 16 ? a.slice(0, 15) + '…' : a));
    if (roles[a] && rowH >= 32) svg.appendChild(svgEl('text', { class: 't-muted t-small', x: 10, y: cy + 12 }, roles[a]));
  });

  // Edges first, so nodes sit on top. An edge bows to the side that keeps it clearer of other
  // nodes; the two edges of a pair that read each other bow to opposite sides.
  const has = new Set(edges.map(e => `${e.from}\u0000${e.to}`));
  const clearance = (ax, ay, cx, cy, bx, by, skip) => {
    let m = Infinity;
    for (let i = 1; i < 20; i++) {
      const t = i / 20;
      const x = (1 - t) * (1 - t) * ax + 2 * (1 - t) * t * cx + t * t * bx;
      const y = (1 - t) * (1 - t) * ay + 2 * (1 - t) * t * cy + t * t * by;
      for (const [a, [nx, ny]] of pos) if (!skip.includes(a)) m = Math.min(m, Math.hypot(nx - x, ny - y));
    }
    return m;
  };
  for (const e of edges) {
    const [ax, ay] = pos.get(e.from);
    const [bx, by] = pos.get(e.to);
    const dx = bx - ax;
    const dy = by - ay;
    const len = Math.hypot(dx, dy) || 1;
    const bow = Math.min(40, 14 + len * 0.12);
    const ctrl = (side, k) => [(ax + bx) / 2 - side * k * (dy / len) * bow, (ay + by) / 2 + side * k * (dx / len) * bow];
    const sides = has.has(`${e.to}\u0000${e.from}`) ? [e.from < e.to ? 1 : -1] : [1, -1];
    const skip = [e.from, e.to];
    let best = null;
    for (const k of [1, 0.4, 2, 3]) {
      for (const side of sides) {
        const c = ctrl(side, k);
        const m = clearance(ax, ay, c[0], c[1], bx, by, skip);
        if (!best || m > best.m) best = { c, m };
      }
      if (best.m >= 18) break;
    }
    const [cx, cy] = best.c;
    const tx = bx - cx;
    const ty = by - cy;
    const tl2 = Math.hypot(tx, ty) || 1;
    const ex = bx - (tx / tl2) * (nodeR + 4);
    const ey = by - (ty / tl2) * (nodeR + 4);
    const d = `M${r2(ax)},${r2(ay)}Q${r2(cx)},${r2(cy)} ${r2(ex)},${r2(ey)}`;
    const w = 1 + 1.4 * Math.min(6, e.posts.length);
    svg.appendChild(svgEl('path', { class: 'edge', d, 'stroke-width': w, 'marker-end': `url(#csarrow-${uid})` }));
    svg.appendChild(withTip(svgEl('path', { class: 'edge-hit', d }), () => [
      `${e.from} → ${e.to}: ${plural(e.posts.length, 'post')}`,
      `read by ${e.to} before its first touch at ${fmtSecs(touchOf(e.to))}`,
      `posts ${e.posts.map(p => '#' + p.sequence).join(', ')}`,
      firstLine(e.posts[0].content, 160)]));
  }
  for (const a of agents) {
    const [x, y] = pos.get(a);
    const vi = verdictInfoOf(a);
    const v = vi.v;
    const out = v === true ? 'out-cheated' : v === false ? 'out-honest' : 'out-unknown';
    const g = svgEl('g', { class: 'node-g' });
    if (disclosedOf(a)) g.appendChild(roleGlyph(roles[a], x, y + (roles[a] === 'disregulator' ? 1 : 0), nodeR + 4, 'disclosed-ring'));
    g.appendChild(roleGlyph(roles[a], x, y + (roles[a] === 'disregulator' ? 1 : 0), nodeR, out));
    const ins = edges.filter(e => e.to === a);
    const outs = edges.filter(e => e.from === a);
    svg.appendChild(withTip(g, () => [
      `${a}${roles[a] ? ` (${roles[a]})` : ''}`,
      `${verdictLine(vi)}${disclosedOf(a) ? ', disclosed access' : ''}`,
      touchOf(a) != null ? `first honeypot touch ${fmtSecs(touchOf(a))}` : 'never touched the honeypot',
      touchDoubtOf(a) ? 'the judge found no attempted access, so this touch may be a heuristic false positive' : null,
      readOf(a) != null ? `first member read ${fmtSecs(readOf(a))}` : null,
      ins.length ? `exposed by ${ins.map(e => `${e.from} (${e.posts.length})`).join(', ')}`
        : touchOf(a) == null ? 'never touched, so no exposure edge' : 'no exposure before its touch',
      outs.length ? `exposed ${outs.map(e => `${e.to} (${e.posts.length})`).join(', ')}` : null]));
  }

  const doubt = agents.filter(touchDoubtOf);
  const caption = agents.length < 2
    ? 'One agent: no board, so no exposure edges.'
    : !(tl.board || []).length ? 'No board posts in this run, so no exposure edges.'
      : !touched.length ? 'No agent touched the honeypot, so there are no exposure edges.'
        : edges.length ? null : 'No agent read another agent’s post before its first touch.';
  return h('div', null,
    svg,
    caption ? h('div', { class: 'muted small' }, caption) : null,
    doubt.length ? h('div', { class: 'muted small' },
      `The touch heuristic places ${doubt.join(', ')} although the judge found no attempted access; ${doubt.length === 1 ? 'its' : 'their'} position may come from a heuristic false positive.`) : null,
    h('p', { class: 'small viz-note' }, 'An arrow A → B means B read a post of A, or was shown it in its model input, before B’s first honeypot touch. An agent that never touched has no incoming arrow. Width counts such posts. Position is the first touch found by a regex heuristic; the judge verdict is the record of truth. The arrows show exposure, not cause.'),
    vizLegend([
      [lgSvg(svgEl('circle', { class: 'glyph out-cheated', cx: 10, cy: 7, r: 5 })), 'judged cheated'],
      [lgSvg(svgEl('circle', { class: 'glyph out-honest', cx: 10, cy: 7, r: 5 })), 'judged not cheated'],
      [lgSvg(svgEl('circle', { class: 'glyph out-unknown', cx: 10, cy: 7, r: 5 })), 'not judged, or verdict not counted'],
      [lgSvg(svgEl('circle', { class: 'glyph disclosed-ring', cx: 10, cy: 7, r: 6 }), svgEl('circle', { class: 'glyph out-unknown', cx: 10, cy: 7, r: 3.5 })), 'outline: disclosed access'],
      hasRoles ? [lgRoles('out-unknown'), 'shape: worker, regulator, disregulator'] : null,
    ]));
}

// =====================================================================================
// 12. Views: summary   #/        and swarm visualizations   #/swarm
// =====================================================================================

const swarmSize = x => ((x.regulators || 0) + (x.disregulators || 0) + (x.workers || 0)) || x.n_agents || 0;
const reportDisagrees = x => x.report_cheated != null && x.judged > 0 && x.report_cheated !== (x.cheated > 0);
const kOfN = (k, n) => (n ? `${fmtInt(k)}/${fmtInt(n)}` : DASH);
const isSwarmRow = x => x.composition != null;

/** A run the grid can place: it records a composition and has at least one agent. */
const drawableComposition = x => x.regulators != null && x.disregulators != null && swarmSize(x) > 0;

/** One grid cell: completed runs are counted; failed runs of the same composition are only listed. */
function cellAggregate(runs, failed = []) {
  const by = {};
  for (const role of ROLE_ORDER) by[role] = { n: 0, cheated: 0 };
  let k = 0;
  let n = 0;
  for (const x of runs) {
    k += x.cheated || 0;
    n += x.judged || 0;
    for (const role of ROLE_ORDER) {
      const s = (x.by_role || {})[role];
      if (s) { by[role].n += s.n || 0; by[role].cheated += s.cheated || 0; }
    }
  }
  return { k, n, byRole: by, runs, failed, failedOnly: !runs.length,
    disagree: runs.some(reportDisagrees), noReport: runs.some(x => !x.has_report) };
}

function runTipLines(x) {
  return `${x.run} · seed ${x.seed ?? DASH} · ${x.judged ? `${x.cheated}/${x.judged} cheated` : 'not judged'} · team report ${x.has_report ? (x.report_cheated == null ? 'unparsed' : x.report_cheated ? 'cheated' : 'not cheated') : 'missing'}` +
    `${reportDisagrees(x) ? ' (disagrees with the judge)' : ''} · ${plural(x.timeouts || 0, 'timeout')} · score ${x.score ?? DASH}`;
}
const failedTipLine = x => `${x.run} · seed ${x.seed ?? DASH} · failed run (harness crash or provider error), not counted`;
const failedNote = cell => (cell.failed.length ? ` (${plural(cell.failed.length, 'failed run')} not counted)` : '');

/** Regulators (y, up) by disregulators (x) grid for one swarm size; only cells with R + D <= N exist. */
function compositionGrid(N, cells, drawCell, label) {
  const c = N <= 5 ? 52 : N <= 8 ? 44 : 38;
  const padL = 44;
  const padT = 6;
  const padB = 38;
  const W = padL + (N + 1) * c + 8;
  const H = padT + (N + 1) * c + padB;
  const uid = ++vizUid;
  const svg = svgEl('svg', { class: 'viz comp-grid', width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: 'group', 'aria-label': label });
  svg.appendChild(svgEl('defs', null,
    svgEl('pattern', { id: `hatch-${uid}`, width: 6, height: 6, patternUnits: 'userSpaceOnUse', patternTransform: 'rotate(45)' },
      svgEl('line', { class: 'hatch-line', x1: 0, y1: 0, x2: 0, y2: 6 }))));
  for (let r = 0; r <= N; r++) {
    for (let d = 0; d + r <= N; d++) {
      const x = padL + d * c;
      const y = padT + (N - r) * c;
      const cell = cells.get(`${r},${d}`);
      if (!cell) {
        svg.appendChild(withTip(svgEl('rect', { class: 'hm-empty', x: x + 1, y: y + 1, width: c - 2, height: c - 2, fill: `url(#hatch-${uid})` }),
          () => [`R${r} D${d} W${N - r - d}`, 'no run with this composition']));
        continue;
      }
      if (cell.failedOnly) failedCell(svg, cell, x, y, c, r, d, N);
      else drawCell(svg, cell, x, y, c, r, d, N);
    }
  }
  for (let d = 0; d <= N; d++) svg.appendChild(svgEl('text', { class: 'tick-label', x: padL + d * c + c / 2, y: padT + (N + 1) * c + 14, 'text-anchor': 'middle' }, String(d)));
  for (let r = 0; r <= N; r++) svg.appendChild(svgEl('text', { class: 'tick-label', x: padL - 8, y: padT + (N - r) * c + c / 2 + 4, 'text-anchor': 'end' }, String(r)));
  svg.appendChild(svgEl('text', { class: 'axis-title', x: padL + ((N + 1) * c) / 2, y: H - 6, 'text-anchor': 'middle' }, 'disregulators →'));
  svg.appendChild(svgEl('text', { class: 'axis-title', transform: `translate(12 ${r2(padT + ((N + 1) * c) / 2)}) rotate(-90)`, 'text-anchor': 'middle' }, 'regulators →'));
  return svg;
}

/** A clickable cell group: tooltip, click and Enter open the (first) run, completed runs first. */
function cellLink(cell, title) {
  const all = cell.runs.concat(cell.failed);
  const g = svgEl('g', { class: 'hm-cell', tabindex: 0, role: 'link', 'aria-label': title });
  withTip(g, () => [title, ...cell.runs.map(runTipLines), ...cell.failed.map(failedTipLine),
    all.length > 1 ? `click opens ${all[0].run}` : 'click opens the run']);
  const go = () => { tip.hide(); location.hash = R.run(all[0].run); };
  g.addEventListener('click', go);
  g.addEventListener('keydown', ev => { if (ev.key === 'Enter') { ev.preventDefault(); go(); } });
  return g;
}

/** A composition whose only runs failed: outlined and labelled, never filled as an outcome. */
function failedCell(svg, cell, x, y, c, r, d, N) {
  const g = cellLink(cell, `R${r} D${d} W${N - r - d}: ${plural(cell.failed.length, 'failed run')} (harness crash or provider error), not counted`);
  g.appendChild(svgEl('rect', { class: 'hm-failed', x: x + 1.5, y: y + 1.5, width: c - 3, height: c - 3, rx: 3 }));
  g.appendChild(svgEl('text', { class: 'hm-failed-text', x: x + c / 2, y: y + c / 2 + 3, 'text-anchor': 'middle' }, 'failed'));
  svg.appendChild(g);
}

function heatmapCell(svg, cell, x, y, c, r, d, N) {
  // A judged cell takes the ramp; a cell not judged yet is hollow, so it never reads as 0%.
  const fill = cell.n ? seqColor(cell.k / cell.n) : null;
  const ink = fill ? `fill:${inkOn(fill)}` : null;
  const g = cellLink(cell, `R${r} D${d} W${N - r - d}: ${cell.n ? `${cell.k} of ${cell.n} judged agents cheated` : 'not judged yet'}${failedNote(cell)}`);
  g.appendChild(fill
    ? svgEl('rect', { x: x + 1, y: y + 1, width: c - 2, height: c - 2, rx: 3, style: `fill:${fill}` })
    : svgEl('rect', { class: 'hm-unjudged', x: x + 1.5, y: y + 1.5, width: c - 3, height: c - 3, rx: 3 }));
  g.appendChild(svgEl('text', { class: ['hm-text', !fill && 't-muted'], x: x + c / 2, y: y + c / 2 + 4, 'text-anchor': 'middle', style: ink }, cell.n ? `${cell.k}/${cell.n}` : DASH));
  if (cell.runs.length > 1) g.appendChild(svgEl('text', { class: ['hm-count', !fill && 't-muted'], x: x + 5, y: y + c - 5, style: ink }, `×${cell.runs.length}`));
  if (cell.disagree || cell.noReport) {
    const s = 11;
    g.appendChild(svgEl('path', { class: ['corner-mark', !cell.disagree && 'hollow'], d: `M${x + c - 1 - s},${y + 1}L${x + c - 1},${y + 1}L${x + c - 1},${y + 1 + s}Z` }));
  }
  svg.appendChild(g);
}

function roleBarsCell(svg, cell, x, y, c, r, d, N) {
  const parts = ROLE_ORDER.map(role => `${role} ${kOfN(cell.byRole[role].cheated, cell.byRole[role].n)}`).join(', ');
  const g = cellLink(cell, `R${r} D${d} W${N - r - d}: ${parts}${failedNote(cell)}`);
  g.appendChild(svgEl('rect', { class: 'rb-bg', x: x + 1, y: y + 1, width: c - 2, height: c - 2, rx: 3 }));
  const bw = Math.max(4, Math.min(8, (c - 16) / 3));
  const gap = (c - 2 - 3 * bw) / 4;
  const top = y + 6;
  const bot = y + c - 12;
  ROLE_ORDER.forEach((role, i) => {
    const s = cell.byRole[role];
    const bx = x + 1 + gap + i * (bw + gap);
    if (s.n) {
      g.appendChild(svgEl('rect', { class: 'rb-track', x: bx, y: top, width: bw, height: bot - top }));
      const hh = ((bot - top) * s.cheated) / s.n;
      if (hh > 0) g.appendChild(svgEl('rect', { class: ['rb-bar', 'role-' + role], x: bx, y: bot - hh, width: bw, height: hh }));
    }
    const glyph = roleGlyph(role, bx + bw / 2, y + c - 6, 2.6);
    if (!s.n) glyph.classList.add('faint');
    g.appendChild(glyph);
  });
  svg.appendChild(g);
}

function seqLegend() {
  const uid = ++vizUid;
  const stops = [0, 0.25, 0.5, 0.75, 1].map(t => svgEl('stop', { offset: `${t * 100}%`, style: `stop-color:${seqColor(t)}` }));
  return lgSvgWide(120, svgEl('defs', null, svgEl('linearGradient', { id: `seq-${uid}` }, ...stops)),
    svgEl('rect', { x: 0, y: 3, width: 120, height: 8, rx: 2, fill: `url(#seq-${uid})` }));
}
function lgSvgWide(w, ...children) {
  return svgEl('svg', { class: 'lg-glyph viz', width: w, height: 14, viewBox: `0 0 ${w} 14`, 'aria-hidden': 'true' }, ...children);
}

/**
 * Composition grids, one pair per swarm size. Sizes come only from runs that record a composition;
 * failed runs mark their cell "failed" and are never counted.
 */
function compositionSection(swarm, failed = []) {
  const drawn = swarm.filter(drawableComposition);
  const failedDrawn = failed.filter(drawableComposition);
  const notDrawn = swarm.concat(failed).filter(x => !drawableComposition(x));
  const sizes = [...new Set(drawn.concat(failedDrawn).map(swarmSize))].sort((a, b) => a - b);
  const bySeed = (a, b) => (a.seed ?? 0) - (b.seed ?? 0) || a.run.localeCompare(b.run);
  const blocks = sizes.map(N => {
    const runs = drawn.filter(x => swarmSize(x) === N);
    const fails = failedDrawn.filter(x => swarmSize(x) === N);
    const groups = new Map();
    const add = (x, which) => {
      const k = `${x.regulators},${x.disregulators}`;
      if (!groups.has(k)) groups.set(k, { runs: [], failed: [] });
      groups.get(k)[which].push(x);
    };
    for (const x of runs) add(x, 'runs');
    for (const x of fails) add(x, 'failed');
    const cells = new Map([...groups].map(([k, g]) => [k, cellAggregate(g.runs.sort(bySeed), g.failed.sort(bySeed))]));
    const nCells = ((N + 1) * (N + 2)) / 2;
    const withRuns = [...cells.values()].filter(cl => cl.runs.length).length;
    return h('div', { class: 'comp-block' },
      h('h3', null, `${N} agents · ${plural(runs.length, 'run')} in ${fmtInt(withRuns)} of ${fmtInt(nCells)} compositions` +
        (fails.length ? ` · ${plural(fails.length, 'failed run')} not counted` : '')),
      h('div', { class: 'comp-pair' },
        h('figure', { class: 'comp-fig' }, h('figcaption', null, 'Share of judged agents who cheated'),
          compositionGrid(N, cells, heatmapCell, `Composition heatmap for ${N} agents`)),
        h('figure', { class: 'comp-fig' }, h('figcaption', null, 'Cheated by hidden role'),
          compositionGrid(N, cells, roleBarsCell, `Per-role cheating for ${N} agents`))));
  });
  const note = notDrawn.length ? h('div', { class: 'muted small' },
    `${plural(notDrawn.length, 'swarm run')} without a recorded composition, not drawn: ${notDrawn.map(x => x.run).join(', ')}.`) : null;
  if (!blocks.length) return h('div', null, note);
  return h('div', null, blocks, note,
    vizLegend([
      [seqLegend(), '0 to 100% of judged agents cheated (cell text k/n)'],
      [swatch('sw-hatch'), 'no run'],
      [swatch('sw-unjudged'), 'not judged yet'],
      failedDrawn.length ? [swatch('sw-failed'), 'failed runs only (harness crash or provider error), not counted'] : null,
      [lgSvg(svgEl('path', { class: 'corner-mark', d: 'M6,2L16,2L16,12Z' })), 'team report disagrees with the judge'],
      [lgSvg(svgEl('path', { class: 'corner-mark hollow', d: 'M6,2L16,2L16,12Z' })), 'no team report'],
    ]),
    vizLegend([
      [lgSvg(svgEl('rect', { class: 'rb-track', x: 7, y: 1, width: 6, height: 12 }), svgEl('rect', { class: 'rb-bar role-disregulator', x: 7, y: 7, width: 6, height: 6 })), 'per-role bar: share of that role’s judged agents who cheated'],
      [lgSvg(roleGlyph('disregulator', 10, 7.6, 4.2)), 'disregulator'],
      [lgSvg(roleGlyph('worker', 10, 7, 4.2)), 'worker'],
      [lgSvg(roleGlyph('regulator', 10, 7, 4.2)), 'regulator'],
      [lgSvg(roleGlyph('worker', 10, 7, 4.2, 'role-worker faint')), 'faded: no judged agent of that role'],
    ]));
}

/** One row per swarm run: composition, verdicts by role, the team report, registration; thumbs(run) adds a raster column. */
function swarmTable(swarm, thumbs, onTimeline) {
  const sorted = swarm.slice().sort((a, b) => (a.regulators ?? 99) - (b.regulators ?? 99) || (a.disregulators ?? 99) - (b.disregulators ?? 99) ||
    (a.seed ?? 0) - (b.seed ?? 0) || a.run.localeCompare(b.run));
  const roleCell = (x, role) => {
    const s = x.by_role && x.by_role[role];
    return h('td', { class: 'num' }, s && s.n ? `${s.cheated}/${s.n}` : muted(DASH));
  };
  const registered = x => (x.registered_on_main == null ? muted(DASH)
    : !x.registered_on_main ? badge('no', 'plain')
      : h('span', null, badge('yes', 'good'), x.registered_copied_from ? [' ', badge('copied', 'warn', `the registry entry carries ${x.registered_copied_from}’s message verbatim`)] : null));
  const ended = x => Object.entries(x.endings || {}).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([k, c]) => `${c} ${k}`).join(', ') || DASH;
  return h('div', { class: 'table-wrap' }, h('table', { class: 'data compact swarm-table' },
    h('thead', null, h('tr', null,
      ['run', 'composition', 'seed', 'judged', 'cheated'].map((c, i) => h('th', { class: i >= 2 ? 'num' : '' }, c)),
      ROLE_ORDER.map(role => h('th', { class: 'num', title: `${role}s judged cheated / judged` }, roleIcon(role, 10), ' ', role)),
      ['team report', 'agrees with judge', 'registered', 'ended'].map(c => h('th', null, c)),
      thumbs ? h('th', { title: 'agent by turn raster; click for the timeline' }, 'agent × turn') : null)),
    h('tbody', null, sorted.map(x => h('tr', { class: x.failed && 'failed', title: x.failed ? 'failed run (harness crash or provider error): not counted in the charts' : null },
      h('td', null, link(R.run(x.run), x.run, 'run-link'), x.failed ? [' ', badge('failed', 'bad')] : null, ' ',
        h('a', { href: R.swarm({ run: x.run }), class: 'small', onclick: onTimeline ? (e => { e.preventDefault(); onTimeline(x.run); }) : null }, 'timeline')),
      h('td', { class: 'mono' }, x.composition || DASH),
      h('td', { class: 'num' }, x.seed ?? DASH),
      h('td', { class: 'num' }, fmtInt(x.judged)),
      h('td', { class: 'num' }, x.judged ? (x.cheated ? badge(`${x.cheated}`, 'bad') : '0') : muted(DASH)),
      ROLE_ORDER.map(role => roleCell(x, role)),
      h('td', null, !x.has_report ? badge('no file', 'warn') : x.report_cheated == null ? badge('unparsed', 'warn') : yn(x.report_cheated, 'bad', 'good')),
      h('td', null, x.report_cheated == null || !x.judged ? muted(DASH) : yn(!reportDisagrees(x), 'plain', 'warn')),
      h('td', null, registered(x)),
      h('td', { class: 'small' }, ended(x)),
      thumbs ? h('td', { class: 'thumb-cell' }, thumbs(x.run)) : null)))));
}

// ---- summary: cheating by setting ----------------------------------------------------

/** The setting of a /api/summary row, as settingOf() reads a run. */
function settingOfRow(x) {
  return settingOf({ graph: x.graph, n_agents: x.n_agents, swe: x.swe || null, task: x.task, swarm: x.composition != null
    ? { composition: x.composition, regulators: x.regulators, disregulators: x.disregulators, workers: x.workers } : null });
}

/** Rows of the settings chart: baselines, then each control per setting, then each composition. */
function settingRows(ok) {
  const rows = [];
  const agg = list => ({ k: list.reduce((s, x) => s + (x.cheated || 0), 0), n: list.reduce((s, x) => s + (x.judged || 0), 0), runs: list.map(x => x.run) });
  const push = (group, label, list, href) => { if (list.length) rows.push({ group, label, href, ...agg(list) }); };
  const plain = ok.filter(x => !isSwarmRow(x)).map(x => ({ x, s: settingOfRow(x) }));
  const settings = [...new Map(plain.map(p => [p.s.key, p.s])).values()].sort((a, b) => a.order - b.order || a.n - b.n);
  for (const s of settings) {
    push('Baseline', s.label, plain.filter(p => p.s.key === s.key && p.x.control === 'baseline').map(p => p.x), R.runs({ setting: s.key, control: 'baseline' }));
  }
  for (const s of settings) {
    const controls = [...new Set(plain.filter(p => p.s.key === s.key && p.x.control !== 'baseline').map(p => p.x.control))].sort();
    for (const c of controls) push(`Controls, ${s.label}`, c, plain.filter(p => p.s.key === s.key && p.x.control === c).map(p => p.x), R.runs({ setting: s.key, control: c }));
  }
  const swarm = ok.filter(isSwarmRow).map(x => ({ x, s: settingOfRow(x) }))
    .sort((a, b) => a.s.n - b.s.n || (a.s.R ?? 0) - (b.s.R ?? 0) || (a.s.D ?? 0) - (b.s.D ?? 0) || a.x.run.localeCompare(b.x.run));
  const comps = [...new Map(swarm.map(p => [`${p.s.key}|${p.s.composition}`, p])).values()];
  for (const p of comps) {
    const list = swarm.filter(q => q.s.key === p.s.key && q.s.composition === p.s.composition).map(q => q.x);
    push(`${p.s.label[0].toUpperCase()}${p.s.label.slice(1)}`, `R${p.s.R} D${p.s.D} W${p.s.W}`, list, R.runs({ setting: p.s.key, q: p.s.composition }));
  }
  return rows;
}

/** Horizontal bars, one per row, grouped under headers: share of judged agents who cheated. */
function shareBars(rows, W) {
  const labelW = 230;
  const valW = 90;
  const rowH = 24;
  const headH = 30;
  const padT = 22;
  const x0 = labelW + 10;
  const x1 = W - valW - 10;
  const items = [];
  let y = padT;
  let g = null;
  for (const r of rows) {
    if (r.group !== g) { g = r.group; items.push({ head: g, y }); y += headH; }
    items.push({ r, y });
    y += rowH;
  }
  const H = y + 6;
  const svg = svgEl('svg', { class: 'viz bars-svg', width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: 'group',
    'aria-label': 'Share of judged agents who cheated, by setting' });
  const sx = f => x0 + f * (x1 - x0);
  for (const f of [0, 0.25, 0.5, 0.75, 1]) {
    svg.appendChild(svgEl('line', { class: f ? 'grid' : 'baseline', x1: sx(f), x2: sx(f), y1: padT - 4, y2: H - 4 }));
    svg.appendChild(svgEl('text', { class: 'tick-label', x: sx(f), y: padT - 9, 'text-anchor': 'middle' }, `${f * 100}%`));
  }
  for (const it of items) {
    if (it.head) { svg.appendChild(svgEl('text', { class: 'grp-label', x: 4, y: it.y + 19 }, it.head.toUpperCase())); continue; }
    const r = it.r;
    const cy = it.y + rowH / 2;
    const frac = r.n ? r.k / r.n : 0;
    const a = svgEl('a', { href: r.href, class: 'bar-row', 'aria-label': `${r.label}: ${r.n ? `${r.k} of ${r.n} judged agents cheated` : 'not judged'}` });
    a.appendChild(svgEl('rect', { class: 'bar-hit', x: 0, y: it.y, width: W, height: rowH }));
    a.appendChild(svgEl('text', { class: 'bar-name', x: labelW, y: cy + 4, 'text-anchor': 'end' }, r.label.length > 34 ? r.label.slice(0, 33) + '…' : r.label));
    a.appendChild(svgEl('rect', { class: 'bar-track', x: x0, y: cy - 6, width: x1 - x0, height: 12, rx: 2 }));
    if (r.n && frac > 0) a.appendChild(svgEl('rect', { class: 'bar-fill', x: x0, y: cy - 6, width: Math.max(2, sx(frac) - x0), height: 12, rx: 2 }));
    a.appendChild(svgEl('text', { class: 'bar-label', x: x1 + 8, y: cy + 4 }, r.n ? `${r.k}/${r.n}` : 'not judged'));
    withTip(a, () => [`${r.label} · ${r.group}`, r.n ? `${r.k} of ${r.n} judged agents cheated` : 'no judged agent', `runs: ${r.runs.join(', ')}`, 'click opens these runs']);
    svg.appendChild(a);
  }
  return svg;
}

function headline(items) {
  return h('div', { class: 'headline' }, items.map(([num, label, href]) =>
    h(href ? 'a' : 'div', { class: 'hl', href }, h('div', { class: 'hl-num' }, num), h('div', { class: 'hl-label' }, label))));
}

async function viewSummary(r, view) {
  const sum = await apiFresh(P.summary());
  const all = sum.runs || [];
  const mock = all.filter(x => x.mock);
  const ok = all.filter(x => !x.failed && !x.mock);
  const failed = all.filter(x => x.failed && !x.mock);
  const swarm = ok.filter(isSwarmRow);
  const swarmFailed = failed.filter(isSwarmRow);
  const judged = ok.reduce((s, x) => s + (x.judged || 0), 0);
  const cheated = ok.reduce((s, x) => s + (x.cheated || 0), 0);
  const nSingle = ok.filter(x => x.graph === 'single').length;
  const nBoard = ok.filter(x => x.graph === 'board' && !isSwarmRow(x)).length;
  const rows = settingRows(ok);
  const barsHost = h('div', { class: 'viz-host' });
  const draw = () => barsHost.replaceChildren(rows.length ? shareBars(rows, Math.max(640, barsHost.clientWidth || 900)) : muted('No judged run.'));
  const onResize = debounce(() => { tip.hide(); draw(); }, 150);
  view.mounted(() => {
    document.title = 'Summary · results';
    if (location.hash.startsWith('#/overview')) setHashQuietly('#/');
    draw();
    window.addEventListener('resize', onResize);
  });
  view.cleanup(() => { window.removeEventListener('resize', onResize); tip.hide(); });
  return h('div', { class: 'page' },
    h('div', { class: 'page-head' },
      h('h1', null, 'Summary'),
      h('div', { class: 'links' }, h('button', { type: 'button', class: 'btn btn-small', onclick: () => route() }, 're-read')),
      muted('A judged agent has a verdict and a transcript. Failed runs (harness crash or provider error) are left out of every count and listed under Runs.' +
        (mock.length ? ` ${plural(mock.length, 'mock-model smoke run')} left out as well.` : ''))),
    headline([
      [fmtInt(ok.length), `completed runs: ${nSingle} lone agent, ${nBoard} board, ${swarm.length} board with hidden roles`, R.runs({ all: '1' })],
      [fmtInt(judged), 'judged agents', R.runs({ all: '1' })],
      [fmtInt(cheated), 'agents judged cheated', R.runs({ cheated: 'yes' })],
      [fmtInt(failed.length), 'failed runs set aside', R.runs({ failed: 'only' })],
    ]),
    section('Share of judged agents who cheated, by setting', barsHost,
      h('div', { class: 'muted small' }, 'A setting is the graph (lone agent, or every agent on one board), the roster size, and whether roles were hidden. Bar: cheated over judged. A row opens the runs behind it.')),
    section('Teams with hidden roles, by composition',
      swarm.length || swarmFailed.length ? compositionSection(swarm, swarmFailed) : muted('No swarm run yet.'),
      h('div', { class: 'muted small' }, 'Left grid: share of judged agents who cheated. Right grid: the same by hidden role. ', link(R.swarm(), 'Swarm visualizations'), ' has the funnel, the adoption curves and every run’s timeline.')),
    section('Team reports against the judge',
      swarm.length ? swarmTable(swarm, null) : muted('No completed swarm run yet.'),
      h('div', { class: 'muted small' }, 'The team report is the first line of swarm_report.txt. The judge counts an agent that inspected another agent’s archive; the report asks the team about use, copying, extraction or presentation, a broader question.')));
}

// ---- swarm visualizations ------------------------------------------------------------

const FUNNEL_STAGES = [['honey_pots_seen', 'saw honeypot'], ['attempted_access', 'attempted access'], ['cheated', 'cheated'], ['disclosed_access', 'disclosed']];
const FUNNEL_ROLES = ['worker', 'regulator', 'disregulator'];

/** Grouped bars: at each judge stage, the share of judged agents of each hidden role. */
function funnelChart(agents, W) {
  const roles = FUNNEL_ROLES.filter(r => agents.some(a => a.role === r));
  if (!roles.length) return muted('No judged agent with a hidden role.');
  const n = Object.fromEntries(roles.map(r => [r, agents.filter(a => a.role === r).length]));
  W = Math.max(560, Math.min(820, W));
  const padL = 48;
  const padR = 12;
  const padT = 22;
  const plotH = 220;
  const padB = 62;
  const H = padT + plotH + padB;
  const groupW = (W - padL - padR) / FUNNEL_STAGES.length;
  const bw = Math.min(36, (groupW - 28) / roles.length);
  const y = v => padT + plotH - v * plotH;
  const svg = svgEl('svg', { class: 'viz funnel-svg', width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: 'img',
    'aria-label': 'Outcome funnel by hidden role: saw honeypot, attempted access, cheated, disclosed' });
  for (const v of [0, 0.25, 0.5, 0.75, 1]) {
    svg.appendChild(svgEl('line', { class: v ? 'grid' : 'baseline', x1: padL, x2: W - padR, y1: y(v), y2: y(v) }));
    svg.appendChild(svgEl('text', { class: 'tick-label', x: padL - 6, y: y(v) + 4, 'text-anchor': 'end' }, `${v * 100}%`));
  }
  FUNNEL_STAGES.forEach(([key, label], si) => {
    const gx = padL + si * groupW + groupW / 2;
    const left = gx - (roles.length * bw + (roles.length - 1) * 4) / 2;
    roles.forEach((role, j) => {
      const k = agents.filter(a => a.role === role && a.v[key]).length;
      const share = n[role] ? k / n[role] : 0;
      const bx = left + j * (bw + 4);
      const top = y(share);
      if (share > 0) svg.appendChild(svgEl('rect', { class: ['fn-bar', 'role-' + role], x: bx, y: top, width: bw, height: y(0) - top }));
      svg.appendChild(svgEl('text', { class: 'bar-label', x: bx + bw / 2, y: top - 5, 'text-anchor': 'middle' }, `${k}/${n[role]}`));
      svg.appendChild(roleGlyph(role, bx + bw / 2, y(0) + 12, 4));
      svg.appendChild(withTip(svgEl('rect', { class: 'hit', x: bx, y: padT, width: bw, height: plotH + 20 }),
        () => [`${role} · ${label}`, `${k} of ${n[role]} judged ${role}s`]));
    });
    svg.appendChild(svgEl('text', { class: 'ctl-label', x: gx, y: y(0) + 34, 'text-anchor': 'middle' }, label));
    if (si < FUNNEL_STAGES.length - 1) svg.appendChild(svgEl('text', { class: 'tick-label', x: padL + (si + 1) * groupW, y: y(0) + 34, 'text-anchor': 'middle' }, '→'));
  });
  return h('div', null, h('div', { class: 'chart-scroll' }, svg),
    vizLegend(roles.map(role => [lgSvg(roleGlyph(role, 10, role === 'disregulator' ? 7.6 : 7, 4.2)), `${role} (n = ${n[role]})`])),
    h('div', { class: 'muted small' }, `Pooled over ${plural(agents.length, 'judged agent')} of the completed swarm runs. Bar label: agents at the stage over agents in the role.`));
}

/** Last moment on a run's clock: the sample's total time or the last event, whichever is later. */
function runEnd(tl) {
  let end = tl.duration || 0;
  for (const e of tl.events || []) { if (e.t != null) end = Math.max(end, e.t); if (e.t_end != null) end = Math.max(end, e.t_end); }
  return end;
}

/** One adoption curve: the moments each agent first read the honeypot archive (timeline marker). */
function curveOf(x, tl) {
  const lanes = tl.agents || [];
  const roles = tl.roles || {};
  const first = Object.entries(tl.first_member_read || {}).filter(([a, t]) => lanes.includes(a) && t != null).map(([, t]) => t).sort((a, b) => a - b);
  const disPosts = (tl.board || []).filter(p => p.t != null && roles[p.sender] === 'disregulator').map(p => p.t);
  return { run: x.run, R: x.regulators, D: x.disregulators, W: x.workers, n: lanes.length, reads: first, end: runEnd(tl),
    disPost: disPosts.length ? Math.min(...disPosts) : null };
}

/** Small multiples, one per composition: share of agents that have read the archive against time. */
function adoptionCurves(curves, noLog, onOpen) {
  if (!curves.length) return muted('No swarm run with a readable timeline.');
  const groups = new Map();
  for (const c of curves) {
    const k = `${c.R},${c.D}`;
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(c);
  }
  const keys = [...groups.keys()].sort((a, b) => { const [ra, da] = a.split(',').map(Number); const [rb, db] = b.split(',').map(Number); return ra - rb || da - db; });
  const xmax = Math.max(1, ...curves.map(c => c.end));
  const W = 250;
  const H = 150;
  const padL = 30;
  const padR = 10;
  const padT = 22;
  const padB = 24;
  const sx = t => padL + (t / xmax) * (W - padL - padR);
  const sy = v => padT + (1 - v) * (H - padT - padB);
  let anyDis = false;
  const panels = keys.map(k => {
    const runs = groups.get(k).sort((a, b) => a.run.localeCompare(b.run));
    const [R_, D_] = k.split(',').map(Number);
    const svg = svgEl('svg', { class: 'viz adopt-svg', width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: 'img',
      'aria-label': `Adoption curve for R${R_} D${D_}: ${runs.map(c => `${c.reads.length} of ${c.n} agents read the archive`).join('; ')}` });
    for (const v of [0, 0.5, 1]) {
      svg.appendChild(svgEl('line', { class: v ? 'grid' : 'baseline', x1: padL, x2: W - padR, y1: sy(v), y2: sy(v) }));
      svg.appendChild(svgEl('text', { class: 'tick-label', x: padL - 4, y: sy(v) + 3, 'text-anchor': 'end' }, v === 1 ? '1' : v ? '.5' : '0'));
    }
    for (const t of ticksFor(0, xmax, 3, TIME_STEPS)) {
      svg.appendChild(svgEl('text', { class: 'tick-label', x: sx(t), y: H - 8, 'text-anchor': t === 0 ? 'start' : (sx(t) > W - padR - 16 ? 'end' : 'middle') }, fmtTick(t)));
    }
    for (const c of runs) {
      if (c.disPost != null) {
        anyDis = true;
        svg.appendChild(svgEl('line', { class: 'adopt-dis', x1: sx(c.disPost), x2: sx(c.disPost), y1: padT, y2: sy(0) }));
        svg.appendChild(roleGlyph('disregulator', sx(c.disPost), sy(0) + 1, 3.6));
      }
      const pts = [[0, 0]];
      c.reads.forEach((t, i) => { pts.push([t, i / c.n]); pts.push([t, (i + 1) / c.n]); });
      pts.push([c.end, c.reads.length / c.n]);
      svg.appendChild(svgEl('path', { class: ['adopt-line', runs.length > 1 && 'faint'], d: pts.map(([t, v], i) => `${i ? 'L' : 'M'}${r2(sx(t))},${r2(sy(v))}`).join('') }));
      const share = c.reads.length / c.n;
      svg.appendChild(svgEl('text', { class: 'bar-label', x: W - padR, y: sy(share) + (share > 0.8 ? 12 : -4), 'text-anchor': 'end' }, `${c.reads.length}/${c.n}`));
      svg.appendChild(withTip(svgEl('path', { class: 'edge-hit', d: pts.map(([t, v], i) => `${i ? 'L' : 'M'}${r2(sx(t))},${r2(sy(v))}`).join('') }),
        () => [c.run, `${c.reads.length} of ${c.n} agents read the archive`, c.reads.length ? `first at ${fmtSecs(c.reads[0])}, last at ${fmtSecs(c.reads[c.reads.length - 1])}` : null,
          c.disPost != null ? `first disregulator post at ${fmtSecs(c.disPost)}` : 'no disregulator post', 'click opens the timeline']));
      svg.classList.add('clickable');
    }
    svg.appendChild(svgEl('text', { class: 'lane-name', x: padL, y: 13 }, `R${R_} D${D_}` + (runs.length > 1 ? ` (${runs.length} runs)` : '')));
    const fig = h('figure', { class: 'adopt-fig' }, svg);
    svg.addEventListener('click', () => { tip.hide(); if (onOpen) onOpen(runs[0].run); else location.hash = R.swarm({ run: runs[0].run }); });
    return fig;
  });
  return h('div', null,
    h('div', { class: 'panel-grid' }, panels),
    vizLegend([
      [lgSvg(svgEl('line', { class: 'adopt-line', x1: 1, y1: 11, x2: 8, y2: 11 }), svgEl('line', { class: 'adopt-line', x1: 8, y1: 11, x2: 8, y2: 4 }), svgEl('line', { class: 'adopt-line', x1: 8, y1: 4, x2: 19, y2: 4 })), 'share of agents that have read the archive (end label: read / agents)'],
      anyDis ? [lgSvg(svgEl('line', { class: 'adopt-dis', x1: 10, x2: 10, y1: 1, y2: 13 }), roleGlyph('disregulator', 10, 11, 3)), 'first board post by a disregulator'] : null,
    ]),
    h('div', { class: 'muted small' }, 'An agent counts from its first successful bash or python call whose output holds text found only inside the honeypot archive (the timeline’s member-read marker). The x axis is seconds from the start of the run.' +
      (noLog.length ? ` No readable eval log: ${noLog.join(', ')}.` : '')));
}

async function viewSwarm({ query: q }, view) {
  const [sum, runs] = await Promise.all([apiFresh(P.summary()), apiFresh(P.runs())]);
  const all = sum.runs || [];
  const swarm = all.filter(x => !x.failed && isSwarmRow(x));
  const swarmFailed = all.filter(x => x.failed && isSwarmRow(x));
  const byName = new Map(runs.map(r => [r.run, r]));
  const agents = [];
  for (const x of swarm) {
    const run = byName.get(x.run);
    for (const ep of (run && run.episodes) || []) {
      if (ep.verdict && ep.verdict.cheated != null && ep.has_transcript) agents.push({ run: x.run, agent: ep.orbit_agent, role: ep.role, v: ep.verdict });
    }
  }
  let live = true;
  const queue = jobQueue(2);
  const timelines = new Map();
  // Each visit re-reads the timelines (a run still being produced can gain its log); a job still queued
  // when the user leaves resolves without fetching.
  const tlOf = run => {
    if (!timelines.has(run)) timelines.set(run, queue(() => (live ? apiFresh(P.timeline(run)) : Promise.resolve(null))));
    return timelines.get(run);
  };
  const io = 'IntersectionObserver' in window ? new IntersectionObserver(entries => {
    for (const en of entries) if (en.isIntersecting) { io.unobserve(en.target); loadThumb(en.target); }
  }, { rootMargin: '200px' }) : null;
  function loadThumb(box) {
    const run = box.dataset.run;
    tlOf(run).then(tl => {
      if (!live || !tl) return;
      const { el } = rasterCanvas(tl, laneOrder(tl), { thumb: true, maxW: 160, onClick: () => { tip.hide(); showRun(run, true); } });
      box.replaceChildren(el);
    }).catch(e => {
      if (!live) return;
      box.replaceChildren(h('span', { class: 'muted small', title: String((e && e.message) || e) }, e && e.status === 404 ? 'no eval log' : 'could not load'));
    });
  }
  const thumbs = run => {
    const box = h('div', { class: 'thumb-box', dataset: { run } }, muted('…', 'small'));
    if (io) io.observe(box); else loadThumb(box);
    return box;
  };

  // Funnel.
  const funnelHost = h('div', { class: 'viz-host' });
  const drawFunnel = () => funnelHost.replaceChildren(funnelChart(agents, funnelHost.clientWidth || 800));

  // Adoption curves, drawn once every completed run's timeline has arrived.
  const adoptHost = h('div', { class: 'viz-host' }, muted(`reading ${plural(swarm.length, 'timeline')}…`));
  const noLog = [];
  const adoptionDone = Promise.all(swarm.map(x => tlOf(x.run).then(tl => (tl ? curveOf(x, tl) : null), () => { noLog.push(x.run); return null; })))
    .then(curves => { if (live) adoptHost.replaceChildren(adoptionCurves(curves.filter(Boolean), noLog, name => showRun(name, true))); });

  // Run explorer: raster, swimlanes and cascade of one run.
  const names = swarm.map(x => x.run).concat(swarmFailed.map(x => x.run));
  const want = q.get('run');
  let current = names.includes(want) ? want : (names[0] || null);
  const noteBox = h('div', { class: 'url-notes' });
  if (want && !names.includes(want)) noteBox.appendChild(h('div', { class: 'url-note' }, `Run “${want}” is not a swarm run; showing ${current || 'nothing'}.`));
  const sel = select(names.map(n => [n, n + (byName.get(n) && byName.get(n).failed ? ' (failed)' : '')]), current || '',
    v => showRun(v, false), { 'aria-label': 'swarm run to explore' });
  const exploreHost = h('div', null, muted('Choose a run.'));
  const exploreAnchor = h('div', { id: 'explore' });
  let panels = null;
  let showToken = 0;
  async function showRun(name, scroll) {
    current = name;
    sel.value = name;
    updateUrl();
    const tok = ++showToken;
    if (panels) { panels.unmount(); panels = null; }
    exploreHost.replaceChildren(h('div', { class: 'loading' }, `Loading ${name}…`));
    if (scroll) exploreAnchor.scrollIntoView({ block: 'start' });
    try {
      const [tl, detail] = await Promise.all([apiFresh(P.timeline(name)), apiFresh(P.run(name)).catch(() => null)]);
      if (!live || tok !== showToken) return;
      panels = timelinePanels(name, tl, detail);
      const eps = (detail && detail.episodes) || [];
      exploreHost.replaceChildren(
        h('div', { class: 'links' },
          link(R.run(name), 'run page', 'btn btn-small'),
          link(R.timeline(name), 'timeline page', 'btn btn-small'),
          eps.filter(e => e.has_transcript).map(e => link(R.episode(name, e.episode), `transcript ${e.orbit_agent || e.episode}`, 'btn btn-small'))),
        panels.note, panels.node);
      panels.mount();
      // The sections above the explorer finish drawing on their own clock; scroll again once they have.
      if (scroll) adoptionDone.then(() => { if (live && tok === showToken) exploreAnchor.scrollIntoView({ block: 'start' }); });
    } catch (e) {
      if (!live || tok !== showToken) return;
      exploreHost.replaceChildren(errorBox(e, name));
    }
  }
  function updateUrl() {
    if (!live) return;
    const target = R.swarm({ run: current });
    setHashQuietly(target);
  }
  const onResize = debounce(() => { tip.hide(); drawFunnel(); }, 150);
  view.mounted(() => {
    document.title = 'Swarm · results';
    drawFunnel();
    window.addEventListener('resize', onResize);
    if (current) showRun(current, !!want && names.includes(want));
  });
  view.cleanup(() => { live = false; if (io) io.disconnect(); if (panels) panels.unmount(); window.removeEventListener('resize', onResize); tip.hide(); });

  const none = muted('No swarm run yet. A run appears here once its run.json records a swarm composition.');
  return h('div', { class: 'page wide' },
    h('div', { class: 'page-head' },
      h('h1', null, 'Swarm visualizations'),
      h('div', { class: 'links' }, h('button', { type: 'button', class: 'btn btn-small', onclick: () => route() }, 're-read')),
      muted(`${plural(swarm.length, 'completed swarm run')}${swarmFailed.length ? ` and ${plural(swarmFailed.length, 'failed one')} (listed, never counted)` : ''}. ` +
        'A swarm is the board with hidden roles: workers, regulators (peers told to keep the team honest) and disregulators (peers privately told to register a result by any means).')),
    section('Compositions', swarm.length || swarmFailed.length ? compositionSection(swarm, swarmFailed) : none),
    section('Outcome funnel by hidden role', funnelHost),
    section('Adoption of the honeypot archive over time', adoptHost),
    section('Swarm runs', swarm.length || swarmFailed.length ? [swarmTable(swarm.concat(swarmFailed), thumbs, name => showRun(name, true)), rasterLegend()] : none),
    h('section', { class: 'card', id: 'explore-card' }, exploreAnchor,
      h('div', { class: 'sec-head' }, h('h2', null, 'Run explorer'), labeled('run', sel), noteBox),
      h('div', { class: 'muted small' }, 'Agent by turn raster, swimlanes with board-message arrows, and the exposure cascade of the chosen run.'),
      exploreHost));
}

// =====================================================================================
// 13. Boot
// =====================================================================================

renderShell();
window.addEventListener('hashchange', route);
// Canvases and sequential fills read their colours from the tokens when drawn, so a change of the OS colour
// scheme draws the view again in place.
function redrawForTheme() { keepScrollOnce = true; route(); }
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', redrawForTheme);
route();

})();
