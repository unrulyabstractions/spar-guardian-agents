/* View: transcript (#/run/<run>/episode/<episode>).
 * The page shows one agent's ATIF steps with their tool calls, results and verdict.
 *
 *   import { viewEpisode } from './view_transcript.js';
 *   const node = await viewEpisode(route, view);
 */
import { asText, debounce, firstLine, fmtInt, h, plural } from './dom.js';
import { P, R, api, setHashQuietly } from './api.js';
import { badge, episodeRecord, extraBadges, isBlockedResult, kv, lazyDetails, link, logLink, longText, mono, muted,
  reasoningView, roleBadge, scrollIntoList, select, splitSignature, toolCallView, urlNotes, verdictBlock } from './components.js';

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

function renderStep(s) {
  const ex = s.extra || {};
  const results = (s.observation && s.observation.results) || [];
  const src = s.source || 'unknown';
  const msg = asText(s.message);
  const flags = new Set();
  if (ex.swarm_turn_rules) flags.add('rules');
  if (ex.withheld_from_model_by_defense) flags.add('withheld');
  if (ex.defense_refusal) flags.add('refusal');
  else if (ex.refusal_text_written_by_model) flags.add('refusal-text');
  for (const r of results) {
    const rx = r.extra || {};
    if (isBlockedResult(rx)) flags.add('blocked');
    else if (rx.is_error || rx.error) flags.add('error');
    if (rx.withheld_from_model_by_defense) flags.add('withheld');
  }
  const providerOnly = flags.has('rules');
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
    extraBadges(ex, ['swarm_turn_rules', 'withheld_from_model_by_defense', 'withheld_text',
      'defense_refusal', 'blocked_output_message_id', 'refusal_text_written_by_model']),
    h('span', { class: 'spacer' }),
    s.llm_call_count != null ? muted(plural(s.llm_call_count, 'model call'), 'small') : null));
  if (providerOnly) {
    el.appendChild(h('div', { class: 'caption caption-notice' },
      `Provider-only: ${ex.swarm_turn_rules} prompt placed by the turn rules, not part of the agent conversation.`));
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
    case 'notice': return e.flags.has('rules');
    default: return e.flags.has(k);
  }
}

export async function viewEpisode({ run, episode, query: q }, view) {
  const [d, runInfo] = await Promise.all([api(P.transcript(run, episode)), api(P.run(run))]);
  const ep = d.episode;
  const t = d.transcript;
  const traj = t.trajectory;
  const steps = traj.steps;
  const orbit = t.orbit;

  // Prev / next only through episodes that have a transcript to show.
  const epNames = runInfo.episodes.filter(e => e.has_transcript).map(e => e.episode);
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
    if (e.flags.has('rules')) counts.notice++;
  }

  const headerCard = h('section', { class: 'card ep-header' },
    h('div', { class: 'ep-title' },
      h('h1', null, episode),
      badge(ep.orbit_agent, 'agent'),
      ep.role ? roleBadge(ep.role) : null,
      t.model ? muted(t.model) : null,
      h('span', { class: 'spacer' }),
      navBtn(prev, `← ${prev || 'prev episode'}`),
      navBtn(next, `${next || 'next episode'} →`)),
    h('div', { class: 'links' },
      link(R.run(run), 'run page', 'btn btn-small'),
      link(R.timeline(run), 'timeline', 'btn btn-small'),
      logLink(runInfo.eval_log_exists, runInfo.eval_log, R.context(run, { agent: ep.orbit_agent }), 'context windows for this agent', 'btn btn-small'),
      logLink(runInfo.eval_log_exists, runInfo.eval_log, R.sample(run), 'sample and workspace', 'btn btn-small')),
    episodeRecord(ep),
    h('div', { class: 'step-counts' },
      `${plural(steps.length, 'step')} · ${plural(counts.tools, 'tool call')}`,
      counts.blocked ? [' · ', badge(`${counts.blocked} BLOCKED`, 'blocked')] : null,
      counts.refusal ? [' · ', badge(`${counts.refusal} REFUSAL`, 'refusal')] : null,
      counts.withheld ? [' · ', badge(`${counts.withheld} WITHHELD`, 'withheld')] : null,
      counts.notice ? [' · ', badge(`${counts.notice} provider-only`, 'notice')] : null,
      counts.error ? [' · ', badge(`${counts.error} tool errors`, 'bad')] : null),
    h('h2', { class: 'sub-h' }, 'Judge verdict'),
    verdictBlock(ep.verdict, runInfo.verdict_fields),
    ep.error ? lazyDetails('episode error', () => longText(ep.error, { lines: 30 }), { cls: 'sub' }) : null,
    lazyDetails('ORBIT metadata and final metrics', () => h('div', null,
      kv([
        ['agent', orbit.agent], ['roster', asText(orbit.roster)], ['topology', orbit.topology],
        ['model calls', orbit.model_calls], ['submit recovered from events', asText(orbit.submit_recovered_from_events)],
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
    scrollIntoList(listBox, e.item);
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
  const notes = urlNotes();
  if (kindVal !== urlKind) notes.show(`Unknown step kind “${urlKind}” in the link; showing all steps.`);
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
    h('div', { class: 'side-tools' }, find, kindSel, notes.box, countEl),
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
      else notes.show(`Step “${startStep}” is not in this transcript.`);
    });
  }
  return page;
}
