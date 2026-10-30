/* Shared components of the results browser.
 * They build the badges, tables, cards, verdict blocks and ladder rungs that several views show.
 *
 *   import { badge, errorBox } from './components.js';
 *   badge('cheated', 'bad');
 */
import { DASH, appendAll, asText, fmtCompact, fmtInt, fmtScore, fmtSecs, fmtUsd, h, plural } from './dom.js';
import { ApiError } from './api.js';
import { roleBadge } from './view_runs.js';

export function badge(text, kind, title) {
  return h('span', { class: ['badge', kind ? 'b-' + kind : 'b-plain'], title }, text);
}

export function muted(text, cls) {
  return h('span', { class: ['muted', cls] }, text);
}

export function mono(text) {
  return h('span', { class: 'mono' }, text == null || text === '' ? DASH : text);
}

export function link(href, text, cls) {
  return h('a', { href, class: cls }, text);
}

/** yes/no badge for a verdict boolean; null renders a dash. */
export function yn(v, yesKind, noKind) {
  if (v === true) return badge('yes', yesKind || 'plain');
  if (v === false) return badge('no', noKind || 'plain');
  return muted(DASH);
}

/** Text in a <pre>; longer than `lines` lines (or `chars` chars) starts collapsed. */
export function longText(text, opts = {}) {
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
export function lazyDetails(summary, build, opts = {}) {
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

export function splitSignature(raw) {
  const s = raw == null ? '' : String(raw);
  const m = SIG_RE.exec(s);
  if (!m || !/[A-Z]/.test(m[1]) || !/[a-z]/.test(m[1]) || !/[0-9]/.test(m[1])) return { text: s, sig: '' };
  return { text: s.slice(0, m.index).replace(/\s+$/, ''), sig: m[1] };
}

export function reasoningView(raw, lines) {
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
export function logLink(ok, evalLog, href, label, cls) {
  if (ok) return link(href, label, cls);
  return h('span', { class: [cls, 'disabled'], title: evalLog ? `eval log missing: ${evalLog}` : 'no eval log recorded for this run' }, label);
}

/** Definition list from [key, value] pairs; undefined values are skipped. */
export function kv(pairs, cls) {
  const dl = h('dl', { class: ['kv', cls] });
  for (const [k, v] of pairs) {
    if (v === undefined) continue;
    dl.appendChild(h('div', { class: 'kv-item' }, h('dt', null, k), h('dd', null, v == null || v === '' ? muted(DASH) : v)));
  }
  return dl;
}

export function section(title, ...children) {
  return h('section', { class: 'card' }, h('h2', null, title), ...children);
}

export function errorBox(err, context) {
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

export function select(options, value, onChange, attrs) {
  const s = h('select', attrs,
    options.map(o => (Array.isArray(o) ? h('option', { value: o[0] }, o[1]) : h('option', { value: o }, o))));
  s.value = value;
  s.addEventListener('change', () => onChange(s.value));
  return s;
}

export function labeled(label, control) {
  return h('label', { class: 'field' }, h('span', { class: 'field-label' }, label), control);
}

/** Tool-call arguments. Bash commands and Python code render as code blocks. */
export function argsView(fn, args) {
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

export function isBlockedResult(rx) {
  return !!rx && typeof rx.error === 'string' && rx.error.startsWith('Blocked by defense');
}

/** Small badges for extra keys that the renderer does not handle itself. */
export function extraBadges(extra, skip) {
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

export function verdictBlock(v) {
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

// The cheating ladder (src/judging/judging_ladder.py): the rung the judge found and the calls and rounds it cites.
const RUNGS = ['exposed', 'tried', 'opened', 'used', 'shipped'];
const RUNG_KIND = { none: 'good', exposed: 'warn', tried: 'bad', opened: 'bad', used: 'bad', shipped: 'bad' };
const BESIDE_LADDER = [['searched', 'warn'], ['tampered', 'bad'], ['admitted', 'good'], ['concealed', 'bad'], ['false_denial', 'bad']];

export function rungBadge(rung) {
  return badge(rung, RUNG_KIND[rung] || 'plain', 'the highest rung of the cheating ladder the judge found');
}

export function ladderBlock(l) {
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

// The code ladder's row of an episode (signals.jsonl, src/measures/code_ladder.py): the rungs by code, no judge.
export function signalsBlock(s) {
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

export function episodeRecord(ep) {
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
