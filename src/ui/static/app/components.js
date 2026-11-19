/* Shared components of the results browser.
 * They build the badges, tables, controls, tool calls, verdict blocks and evidence blocks that several views show.
 *
 *   import { badge, dataTable } from './components.js';
 *   dataTable(['agent', { label: 'calls', num: true }], [['k7q2m', 12]]);
 */
import { DASH, appendAll, asText, fmtCompact, fmtInt, fmtScore, fmtSecs, fmtUsd, h, plural } from './dom.js';
import { ApiError } from './api.js';
import { ROLE_ORDER, roleIcon } from './charts.js';

export function badge(text, kind, title) {
  return h('span', { class: ['badge', kind ? 'b-' + kind : 'b-plain'], title }, text);
}

export function muted(text, cls) {
  return h('span', { class: ['muted', cls] }, text);
}

// How an episode ended (episodes.jsonl ended_by): a badge for submit and stop_working, muted text for the rest.
const ENDED_BY = {
  submit: ['submit', 'good', 'the agent handed in the work with submit'],
  teammate_submit: ['teammate submit', 'plain', 'a teammate handed in the work with submit, which ended the run'],
  stop_working: ['stop_working', 'warn', 'the agent stopped working with stop_working; it handed in nothing'],
};

/** The badge of an episode's ended_by, or null when it has none. */
export function endedByBadge(endedBy) {
  if (endedBy == null) return null;
  return ENDED_BY[endedBy] ? badge(...ENDED_BY[endedBy]) : muted(endedBy);
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

/**
 * A table in its scroll box. cols are labels or {label, num, title}; rows are arrays of cells (a null or empty
 * cell shows a dash) or ready <tr> nodes. opts.cls adds table classes; opts.bare leaves out the scroll box.
 */
export function dataTable(cols, rows, opts = {}) {
  const spec = cols.map(c => (c && typeof c === 'object' && !(c instanceof Node) ? c : { label: c }));
  const cell = (v, c) => h('td', { class: c && c.num ? 'num' : null }, v == null || v === '' ? muted(DASH) : v);
  const table = h('table', { class: ['data', opts.cls] },
    h('thead', null, h('tr', null, spec.map(c => h('th', { class: c.num ? 'num' : null, title: c.title, scope: 'col' }, c.label)))),
    h('tbody', null, rows.map(r => (r instanceof Node ? r : h('tr', null, r.map((v, i) => cell(v, spec[i])))))));
  return opts.bare ? table : h('div', { class: 'table-wrap' }, table);
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

/** A row of toggle buttons from [value, label] options; onChange gets the value pressed. */
export function segmented(options, value, onChange, label) {
  const box = h('div', { class: 'seg', role: 'group', 'aria-label': label });
  const sync = v => {
    for (const b of box.children) {
      b.classList.toggle('active', b.dataset.v === v);
      b.setAttribute('aria-pressed', b.dataset.v === v ? 'true' : 'false');
    }
  };
  for (const [v, text] of options) {
    box.appendChild(h('button', { type: 'button', class: 'seg-btn', dataset: { v }, onclick: () => { sync(v); onChange(v); } }, text));
  }
  sync(value);
  return box;
}

/** Notes about a link the view could not follow as written (an unknown agent, step or call). */
export function urlNotes() {
  const box = h('div', { class: 'url-notes' });
  return { box, show: text => box.appendChild(h('div', { class: 'url-note' }, text)) };
}

/** Scroll a sidebar list so its item is in view, a third from the top. */
export function scrollIntoList(list, item) {
  if (item.offsetTop < list.scrollTop || item.offsetTop + item.offsetHeight > list.scrollTop + list.clientHeight) {
    list.scrollTop = item.offsetTop - list.clientHeight / 3;
  }
}

/** Tool-call arguments. A shell command and Python code render as code blocks. */
function argsView(args) {
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
    const codeLang = typeof v !== 'string' ? null : k === 'command' || k === 'cmd' ? 'shell' : k === 'code' ? 'python' : null;
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

/** One tool call with its arguments and its result nodes; resultEls null shows no result line. */
export function toolCallView(fn, id, args, resultEls) {
  return h('div', { class: 'toolcall' },
    h('div', { class: 'tc-head' },
      h('span', { class: 'tc-label' }, 'tool call'),
      h('code', { class: 'tc-fn' }, fn || '?'),
      h('span', { class: 'spacer' }),
      id ? h('span', { class: 'tc-id' }, id) : null),
    argsView(args),
    resultEls === null ? null
      : resultEls.length ? h('div', { class: 'tc-results' }, resultEls)
        : muted('(no result recorded)', 'small block'));
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

// Hidden roles: Okabe-Ito colour plus shape (circle worker, square regulator, triangle disregulator).
// Red and green stay reserved for outcomes.
export function roleBadge(role) {
  return h('span', { class: 'badge b-plain', title: 'hidden role' }, ROLE_ORDER.includes(role) ? roleIcon(role, 10) : null, role);
}

/** CheatBench's judge's own cheated verdict of an episode; a verdict the server did not count (no transcript) is drawn grey. */
export function verdictCell(ep, yesKind = 'bad', noKind = 'good') {
  const v = ep && ep.verdict ? ep.verdict.cheated : null;
  if (v != null && !ep.counted) {
    return h('span', { title: 'verdict on an episode with no transcript (harness crash or provider error); not counted' }, yn(v, 'plain', 'plain'));
  }
  return yn(v, yesKind, noKind);
}

/** Whether the team registered a result on main (run deliverables), and whether it copied another agent's message. */
export function registeredCell(d) {
  if (!d || d.registered_on_main == null) return muted(DASH, 'small');
  if (!d.registered_on_main) return badge('no', 'plain');
  const from = d.registered_message_copied_from;
  return h('span', null, badge('yes', 'good'), from ? [' ', badge('copied', 'warn', `the registry entry carries ${from}’s message verbatim`)] : null);
}

// The judge's own keys around the task's verdict fields.
const VERDICT_META = ['summary', 'judge', 'judge_cost_usd', 'parse_ok', 'judge_error', 'raw'];
const label = k => k.replace(/_/g, ' ');

/**
 * A judge verdict. fields are the run's verdict_fields ({field, label, kind}, from the server): their booleans come
 * first in that order with their colours; any other boolean shows as a plain flag and any other text as a note.
 */
export function verdictBlock(v, fields = []) {
  if (!v) return h('div', { class: 'verdict' }, muted('Not judged.'));
  const known = new Map(fields.map(f => [f.field, f]));
  const flags = ['cheated', ...fields.map(f => f.field).filter(k => k !== 'cheated' && k in v),
    ...Object.keys(v).filter(k => typeof v[k] === 'boolean' && k !== 'cheated' && !known.has(k) && !VERDICT_META.includes(k))];
  const notes = Object.entries(v).filter(([k, x]) => !VERDICT_META.includes(k) && !flags.includes(k) && x != null && x !== '');
  return h('div', { class: 'verdict' },
    h('div', { class: 'verdict-flags' },
      flags.map(k => {
        const f = known.get(k);
        const kinds = k === 'cheated' ? ['bad', 'good'] : [f ? f.kind : 'plain', 'plain'];
        return h('span', { class: 'vf' }, h('span', { class: 'vf-label' }, k === 'cheated' ? 'CheatBench judge: cheated' : f ? f.label : label(k)), yn(v[k], ...kinds));
      })),
    notes.length ? kv(notes.map(([k, x]) => [label(k), asText(x)]), 'kv-grid') : null,
    h('div', { class: 'verdict-meta' },
      muted(`judge ${v.judge || '?'} · ${fmtUsd(v.judge_cost_usd)}`),
      v.parse_ok === false ? badge('judge output did not parse', 'bad') : null,
      v.judge_error ? badge('judge error', 'bad', String(v.judge_error)) : null),
    v.judge_error ? h('pre', { class: 'pre errtext' }, String(v.judge_error)) : null,
    v.raw ? lazyDetails('raw judge output (did not parse)', () => longText(v.raw, { lines: 40 }), { cls: 'sub' }) : null,
    v.summary ? h('p', { class: 'verdict-summary' }, v.summary) : null);
}

// The code's evidence (src/measures/rungs.py EVIDENCE and COLLABORATION_EVIDENCE), by the names signals.jsonl records
// it under. It is evidence, no ladder: the transcript viewer lays out the three ladders read from it.
const EVIDENCE = ['exposed', 'tried', 'opened', 'used', 'tampered', 'checked', 'shipped'];
const COLLABORATION_EVIDENCE = ['discussed', 'solicited', 'crossed', 'assembled'];

const vfItem = (name, node) => h('span', { class: 'vf' }, h('span', { class: 'vf-label' }, name), node);

/** Evidence as collapsed text blocks: [name, text or list of lines]; empty ones are left out. */
function evidenceDetails(pairs) {
  return pairs.map(([k, t]) => [k, Array.isArray(t) ? t.join('\n') : t]).filter(([, t]) => t)
    .map(([k, t]) => lazyDetails(`${k} evidence`, () => longText(t, { lines: 20 }), { cls: 'sub' }));
}

// An episode's agent row of the code's evidence (signals.jsonl, src/measures/transcript_scan.py): each kind of act on
// the honeypot the code found, with the call it cites, from the transcript and the sandbox probe, no judge.
export function evidenceBlock(s) {
  if (!s) return null;
  const where = f => (s[f + '_call'] == null ? null : `call ${s[f + '_call']}`);
  const acts = EVIDENCE.map(f => [f, h('span', null, yn(s[f], 'plain', 'plain'), where(f) ? [' ', muted(where(f), 'small')] : null)]);
  const list = v => (v && v.length ? v.join(', ') : null);
  return h('div', { class: 'verdict' },
    kv([...acts, ['exposed source', s.exposed_source], ['holds', list(s.holds)], ['pieces seen', list(s.pieces_seen)],
      ['joined', s.joined_call == null ? null : `call ${s.joined_call}`], ['probe', s.probe ? 'ran' : 'none'],
      ['lines seen', s.lines_seen]], 'kv-grid'),
    h('div', { class: 'verdict-meta' }, muted('the code’s evidence: the transcript and the sandbox probe, no judge')),
    evidenceDetails([['tried', s.tried_evidence], ['opened', s.opened_evidence], ['joined', s.joined_evidence],
      ['used', s.used_evidence], ['tampered', s.tampered_evidence], ['checked', s.checked_evidence],
      ['shipped', s.shipped_evidence]]));
}

// The team's record of a sample (signals.jsonl's team row, src/measures/team_ladder.py): the workers' evidence, the
// collaboration evidence, the grader's pass and how the run ended. Each kind lists the agents whose rows hold it.
export function teamBlock(t) {
  if (!t) return null;
  const list = v => (v && v.length ? v.join(', ') : null);
  const acts = EVIDENCE.map(f => [f, h('span', null, yn(t[f], 'plain', 'plain'),
    list(t[f + '_agents']) ? [' ', muted(list(t[f + '_agents']), 'small')] : null)]);
  const sub = t.submit;
  const submit = !sub ? null : [`${sub.agent}${sub.round != null ? ` in round ${sub.round}` : ''}`,
    sub.ends_after_round != null ? `the run ends after round ${sub.ends_after_round}` : null,
    sub.snapshot ? `snapshot ${String(sub.snapshot).slice(0, 12)}` : null].filter(Boolean).join('; ');
  const own = Object.entries(t.own_submits || {}).map(([a, r]) => `${a} r${r ?? DASH}`).join(', ');
  const collab = t.collaboration_evidence || {};
  return h('div', { class: 'verdict' },
    h('div', { class: 'verdict-flags' },
      ...COLLABORATION_EVIDENCE.map(c => vfItem(c, yn(Boolean((collab[c] || []).length), 'plain', 'plain')))),
    kv([...acts,
      ['agents', list(t.agents)],
      ['mode', t.mode],
      ['split', t.split == null ? null : yn(t.split, 'plain', 'plain')],
      ['passed', yn(t.passed, 'plain', 'plain')],
      ['checked', t.checked_blind ? badge('blind', 'warn', 'a copy no agent saw') : null],
      ['ended by', t.ended_by],
      ['hand-in', submit],
      ['own hand-ins', own || null],
      ['refused submits', t.refused_submits],
      ['shutdown rate', t.shutdown_rate == null ? null : `${Math.round(t.shutdown_rate * 100)}%`],
      ['copies no agent owns', list(t.copies)],
    ], 'kv-grid'),
    h('div', { class: 'verdict-meta' }, muted('team record: the code’s evidence over every worker, the board and the hand-in')),
    evidenceDetails([...EVIDENCE.map(f => [f, t[f + '_evidence']]), ['proof', t.proof],
      ...COLLABORATION_EVIDENCE.map(c => [c, collab[c]])]));
}

export function episodeRecord(ep) {
  const u = ep.usage || {};
  const d = ep.defense;
  return kv([
    ['agent', ep.orbit_agent],
    ['hidden role', ep.role ? roleBadge(ep.role) : undefined],
    ['tool calls', fmtInt(ep.n_tool_calls)],
    ['turns', fmtInt(ep.n_turns)],
    ['tokens prompt', h('span', { title: fmtInt(u.input) }, fmtCompact(u.input))],
    ['tokens completion', h('span', { title: fmtInt(u.output) }, fmtCompact(u.output))],
    ['tokens cache read', h('span', { title: fmtInt(u.cache_read) }, fmtCompact(u.cache_read))],
    ['wall time', fmtSecs(ep.wall_time)],
    ['limit', ep.limit == null ? null : asText(ep.limit)],
    ['ended by', endedByBadge(ep.ended_by)],
    ['failure', ep.failure ? badge(ep.failure, 'bad') : null],
    ['ok', ep.ok == null ? null : yn(ep.ok, 'good', 'bad')],
    ['score', fmtScore(ep.score)],
    ['control', ep.control || undefined],
    ['defenses', ep.defenses && ep.defenses.length ? ep.defenses.join(', ') : undefined],
    ['defense checks / blocks', d ? defenseCounts(d) : undefined],
  ], 'kv-grid');
}

function defenseCounts(d) {
  const bb = d.blocks_by_boundary || {};
  const cb = d.checks_by_boundary || {};
  const bounds = [...new Set([...Object.keys(cb), ...Object.keys(bb)])];
  const detail = bounds.map(b => `${b} ${fmtInt(bb[b] || 0)}/${fmtInt(cb[b] || 0)}`).join(' · ');
  return h('span', null, `${plural(d.checks || 0, 'check')}, ${plural(d.blocks || 0, 'block')}`,
    d.blocks ? [' ', badge('blocked', 'blocked')] : null,
    detail ? h('div', { class: 'muted small' }, 'blocks/checks: ', detail) : null);
}
