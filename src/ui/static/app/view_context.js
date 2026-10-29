/* View: context windows (#/run/<run>/context?agent=<name>&call=<i>).
 * The page shows the exact provider input of every model call in the eval log.
 *
 *   import { viewContext } from './view_context.js';
 *   const node = await viewContext(route, view);
 */
import { asText, firstLine, fmtCompact, fmtInt, fmtSecs, fmtTime, h, plural } from './dom.js';
import { P, R, api, apiCall, setHashQuietly } from './api.js';
import { badge, errorBox, kv, labeled, lazyDetails, longText, muted, reasoningView, select } from './components.js';
import { toolCallView } from './view_transcript.js';

// A call's flags are the union of its input messages' flags (plus the output being the
// refusal sentence). They describe the history the call saw, not what happened to its output.
const CALL_FLAG_LABEL = {
  turn_rules: ['turn rules in input', 'notice', 'the turn rules placed a board notice or continue prompt in this input'],
  redacted: ['redacted input', 'withheld', 'a defense withheld a message of this input from the model'],
  refusal_in_history: ['refusal in history', 'plain', 'the input holds the refusal sentence: a substituted completion or the model’s own text'],
  output_blocked: ['output blocked', 'blocked', 'a defense blocked this call’s completion and substituted the refusal sentence'],
};
const CALL_FILTERS = [
  ['', 'any'],
  ['output_blocked', 'output blocked by a defense'],
  ['turn_rules', 'turn rules in input'],
  ['redacted', 'redacted input'],
  ['refusal_in_history', 'refusal in history'],
  ['error', 'has error'],
];
const MSG_FLAG_BADGE = {
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
  const notice = flags.includes('turn_rules');
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
      'Provider-only: board notice or continue prompt placed by the turn rules.'));
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

export async function viewContext({ run, query: q }, view) {
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
