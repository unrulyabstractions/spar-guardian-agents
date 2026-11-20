/* View: one run (#/run/<collection>/<run key>?agent=<id>&call=<k>, ?agent=<id>&label=<judge label>, ?tab=board&a=&b=
 * &mode=&pos= or ?tab=details; with &fold=calls,user,reasoning,call,result and &board=only on an agent's tab).
 * Ported from collusion-hermaneutics' viewer (viewer/static/app/view_run.js). A bar with the run, an agent tab per
 * agent, a Board tab and a Details tab; the selected agent's whole episode below it, each step with its labels
 * (tv_case_view.js); the run record under it: the run's labels, the run settings, each agent's record with its
 * Cheating evidence, its judge labels and CheatBench's judge on it.
 * Our edits: the imports (dom.js, components.js and charts.js are the results browser's own); the judges are read
 * from the run's verdict files (judges/<pass>.<judge>.jsonl), shown where the run has them; the source's API judges,
 * second judge and sentence lens are gone; a regulator and a disregulator are influencers; the header names the
 * collection and the eval log; the Board tab (tv_view_board.js) holds the message board, two agents' calls on one
 * clock with the board between them; the Details tab holds the Cheating, Coordination and Collusion panels and the
 * judge labels. Every Cheating cell holds the code's word and CheatBench's judge's under it (tv_levels.js
 * cheatingCell). A judge label's link opens the place code located its excerpt, highlighted: the agent's tab at that
 * model call's reasoning, or the Board tab at that post (openExcerpt; ?agent=&label= opens it on arrival). CheatBench's
 * own field for an agent that acknowledged its access shows only in the CheatBench judge's block, as "CheatBench:
 * disclosed access". The run record keeps its headings, tables, chips and evidence; every definition is on the Ladders
 * page. A tab beside the agents' is one entry of `views` (a key, its label and a function that draws it once, answering its
 * element and, for the Board, a promise of its handle). */
import { appendAll, enc, fmtInt, fmtUsd, h } from '/static/app/dom.js';
import { badge, dataTable, episodeRecord, kv, lazyDetails, longText, muted } from '/static/app/components.js';
import { ROLE_ORDER, roleIcon } from '/static/app/charts.js';
import { CHEATBENCH, ROLE_LABEL, armLabel, collectionOf, influencerParts, isInfluencer, modelLabel, whoChips,
  taskLabel } from './tv_labels.js';
import { alarmBadge, cheatbenchTeam, cheatbenchWords, cheatingCell, cheatingPanel, clauseList, collusionPanel,
  coordinationPanel, judgeLabels, judgeLabelsPanel, labelEntry, levelChip, levelsPanel, teamScore } from './tv_levels.js';
import { PARTS, centreVertically, renderCase, stripLegend } from './tv_case_view.js';
import { renderBoard } from './tv_view_board.js';

// The run page fills the window under the site's bar (tv_style.css .rp-page): its top is the site bar's real bottom
// edge, which wraps in a narrower window.
function placePage() {
  const page = document.querySelector('.rp-page');
  const top = document.querySelector('.topbar');
  if (page && top) page.style.setProperty('--rp-top', `${Math.round(top.getBoundingClientRect().bottom)}px`);
}
window.addEventListener('resize', placePage, { passive: true });

function flash(el) {
  if (!el) return;
  centreVertically(el);
  el.classList.remove('flash');
  void el.offsetWidth;
  el.classList.add('flash');
}

function roleTag(role) {
  return h('span', { class: ['badge', 'b-plain', 'role-tag', 'role-' + role], title: 'the agent\'s role in the run' },
    roleIcon(ROLE_ORDER.includes(role) ? role : 'worker', 10), ' ', ROLE_LABEL[role] || role);
}

const fieldText = v => (v === true ? 'yes' : v === false ? 'no' : typeof v === 'string' ? v : JSON.stringify(v));
/** A field with nothing in it: null, an empty string or an empty object or list. */
const isEmpty = v => v == null || v === '' || (typeof v === 'object' && !Object.keys(v).length);
/** A key-value grid in columns: a pair of middle length takes two columns (kv-mid), a long one the whole row
 * (kv-wide), so every value stays on its label's line and the short ones keep their columns. */
function wideItems(dl) {
  for (const it of dl.children) {
    const n = it.textContent.length;
    if (n > 70) it.classList.add('kv-wide');
    else if (n > 32) it.classList.add('kv-mid');
  }
  return dl;
}

export async function viewRun(id, q, view) {
  const r = await fetch('/api/run?id=' + enc(id));
  const d = await r.json();
  if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
  const row = d.row;
  const agentIds = d.agents.map(a => a.agent);
  const roles = Object.fromEntries(d.agents.map(a => [a.agent, a.role]));
  const multi = d.agents.length > 1;
  const cheatbench = Object.fromEntries(d.agents.map(a => [a.agent, a.cheatbench]));

  // ---- board: every message by the label the evidence, the Board tab and the judges give it, P1, P2, ... in board
  // order, harness notices included (board_no).
  const posts = new Map(d.boards.flatMap(b => b.messages.map(m => [`P${m.board_no}`, m])));
  const cheating = (d.levels && d.levels.cheating) || {};

  // Calls that read a regulator's post (a worker), or that posted as the regulator (the regulator).
  function regulatorCalls(agent, role) {
    const out = new Set();
    for (const b of d.boards) for (const m of b.messages) {
      if (m.role !== 'regulator') continue;
      if (role === 'regulator' && m.sent && m.sent.agent === agent) out.add(m.sent.call_id);
      if (role !== 'regulator') for (const rd of m.reads) if (rd.agent === agent) out.add(rd.call_id);
    }
    return out;
  }

  // ---- agent cards with lazy transcripts.
  const cards = new Map();
  function agentCard(a) {
    const card = h('section', { class: ['card', 'agent-card', 'role-' + a.role], id: `agent-${a.agent}` });
    const holder = h('div', { class: 'tr-holder' });
    const state = { loaded: null, tr: null };
    const sizeTxt = a.transcript_bytes != null ? `${fmtInt(Math.round(a.transcript_bytes / 1024))} KB` : 'missing';
    const btn = h('button', { type: 'button', class: 'btn', disabled: !a.folder }, a.folder ? `Transcript (${sizeTxt})` : 'No transcript on disk');
    const load = () => {
      if (!state.loaded) {
        btn.disabled = true;
        btn.textContent = 'Loading transcript…';
        const tools = fetch(`/api/tools?id=${enc(id)}&agent=${enc(a.agent)}`).then(x => x.ok ? x.json() : null).catch(() => null);
        state.loaded = fetch(`/api/transcript?id=${enc(id)}&agent=${enc(a.agent)}`)
          .then(x => { if (!x.ok) throw new Error(`HTTP ${x.status}`); return x.json(); })
          .then(t => tools.then(tl => [t, tl]))
          .then(([t, tl]) => {
            state.tr = renderCase(t, { agent: a.agent, levels: d.levels, isRegulator: a.role === 'regulator', regulatorCalls: regulatorCalls(a.agent, a.role), tools: tl });
            btn.disabled = false;
            btn.textContent = `Transcript (${sizeTxt})`;
            return state.tr;
          })
          .catch(e => { holder.replaceChildren(h('div', { class: 'error-box' }, `Could not load the transcript: ${e.message}`)); btn.disabled = false; btn.textContent = 'Retry'; state.loaded = null; throw e; });
      }
      holder.hidden = false;
      return state.loaded;
    };
    btn.addEventListener('click', () => { selectTab(a.agent); });
    appendAll(card, [
      h('div', { class: 'ep-card-head' },
        h('h2', { class: 'agent-h' }, `Agent ${a.agent}`), roleTag(a.role),
        a.episode.model ? muted(a.episode.model, 'small') : null,
        h('span', { class: 'spacer' }), btn),
      wideItems(episodeRecord({ ...a.episode, orbit_agent: undefined })),   // the card's title names the agent
      h('div', { class: 'cb-grid' },
        agentCheating(a.agent),
        agentJudgeLabels(a.agent),
        h('div', null, h('h3', { class: 'sub-h' }, CHEATBENCH.name), cheatbenchBlock(a))),
      a.episode.final_text ? lazyDetails('final text of the agent', () => longText(a.episode.final_text, { lines: 40 }), { cls: 'sub' }) : null,
      holder]);
    cards.set(a.agent, { card, load, state });
    return card;
  }

  async function gotoCall(agent, k) {
    const c = cards.get(agent);
    if (!c) return;
    const tr = await selectTab(agent);
    const box = tr.findCall(k);
    if (box) flash(box);
    else flash(c.card);
  }
  // A post opens in the Board tab.
  const openPost = label => selectTab('board').then(b => b.showPost(label)).catch(() => {});

  // ---- the judge labels: each agent's score, excerpt and the place code located it (the measure's).
  const judged = d.levels.judge_labels.agents;
  const labelAbout = d.levels.judge_label_about.labels;
  // The place of an agent's excerpt, opened with the excerpt highlighted: the agent's tab at the model call whose
  // reasoning holds it, or the Board tab at the post.
  function openExcerpt(agent, label) {
    const e = (judged[agent] || {})[label];
    const at = e && e.located;
    if (!at) return Promise.resolve(null);
    if (at.where === 'post') return selectTab('board').then(b => b.showPost(at.post));
    return selectTab(agent).then(tr => tr.showReasoning(at.call));
  }
  // The link a label's entry shows: where the excerpt is, opening it.
  const openLink = (agent, label) => at => h('button', { type: 'button', class: 'linkish ev-link sc-open',
    title: at.where === 'post' ? `open ${at.post} with the excerpt marked` : `open model call ${at.call} with the excerpt marked`,
    onclick: () => openExcerpt(agent, label).catch(() => {}) },
  at.where === 'post' ? `${at.post} on the board` : `reasoning, model call ${at.call}`);

  // Evidence text with every [k] linked to call k of the agent it names, and every post (P<n>) to the board.
  function evidenceText(text, agent) {
    const s = String(text == null ? '' : text);
    let who = agent;
    const lead = /^([A-Za-z0-9_-]+): /.exec(s);
    if (lead && agentIds.includes(lead[1])) who = lead[1];
    const out = [];
    const byInfluencer = new Set();
    let last = 0;
    const re = /\[(\d+)\]|\bP\d+\b/g;
    let m;
    while ((m = re.exec(s))) {
      if (m.index > last) out.push(s.slice(last, m.index));
      if (m[1] != null && who && agentIds.includes(who)) {
        const k = parseInt(m[1], 10);
        out.push(h('button', { type: 'button', class: 'linkish ev-link', title: `open call ${k} in ${who}'s transcript`, onclick: () => gotoCall(who, k) }, m[0]));
      } else if (m[1] == null && posts.has(m[0])) {
        const label = m[0];
        const p = posts.get(label);
        out.push(h('button', { type: 'button', class: 'linkish ev-link', title: 'show this post on the board', onclick: () => openPost(label) }, label));
        if (isInfluencer(p.role)) byInfluencer.add(p.role);
      } else out.push(m[0]);
      last = re.lastIndex;
    }
    if (last < s.length) out.push(s.slice(last));
    for (const role of byInfluencer) out.push(' ', badge(`written by the ${role}`, 'notice', `the cited post is the ${role}'s`));
    return h('span', { class: 'ev-text' }, out);
  }
  const evidenceList = (lines, agent) => {
    const list = (Array.isArray(lines) ? lines : [lines]).filter(x => x);
    if (!list.length) return null;
    return h('ul', { class: 'ev-list' }, list.map(x => h('li', null, evidenceText(x, agent))));
  };

  function agentCheating(agent) {
    const r = cheating.applicable && cheating.agents[agent];
    if (!r) return muted(`Cheating: ${cheating.reason || 'no row for this agent'}.`, 'small block');
    const L = d.levels.ladders.cheating;
    return h('div', null, h('h3', { class: 'sub-h' }, 'Cheating'),
      h('div', { class: 'verdict-flags' }, cheatingCell(L, r.rung, r.cheated, cheatbenchWords(cheatbench[agent]), true,
        d.levels.agents[agent].cheating.facts)),
      clauseList(d.levels, r.clauses, agent, { evidence: evidenceText }),
      (r.below || []).length ? h('div', { class: 'small' }, h('span', { class: 'field-label', title: 'Evidence only, no rung' }, 'Below the ladder: '),
        evidenceList(r.below, agent)) : null,
      h('div', { class: 'small' }, h('span', { class: 'field-label' }, 'CheatBench labels: '), judgeLabels(r)));
  }

  // The agent's judge labels beside the ladders: each label's score, excerpt, reason and the link to its place.
  function agentJudgeLabels(agent) {
    return h('div', null, h('h3', { class: 'sub-h' }, 'Judge labels'),
      judged[agent] ? h('div', { class: 'sc-pair' }, Object.entries(labelAbout).map(([k, about]) => h('div', { class: 'sc-pair-row' },
        h('span', { class: 'field-label' }, about.name), labelEntry(about, judged[agent][k], openLink(agent, k), true))))
        : muted('not judged', 'small block'));
  }

  function cheatbenchBlock(a) {
    const judges = Object.entries(a.cheatbench || {});
    if (!judges.length) return muted('not judged', 'small block');
    return judges.map(([judge, v]) => h('div', { class: 'judge-block' },
      h('div', { class: 'jb-head' }, h('h4', null, `judge ${judge}`), cheatbenchWords({ [judge]: v })),
      v.summary ? h('p', { class: 'verdict-summary' }, evidenceText(v.summary, a.agent)) : null,
      wideItems(h('dl', { class: 'kv kv-grid' }, Object.entries(v).filter(([k, x]) => !['agent', 'summary'].includes(k) && !isEmpty(x)).map(([k, x]) =>
        h('div', { class: 'kv-item' }, h('dt', null, k === d.cheatbench_disclosed ? 'CheatBench: disclosed access' : k.replace(/_/g, ' ')),
          h('dd', null, k === 'judge_cost_usd' ? fmtUsd(x) : typeof x === 'string' ? evidenceText(x, a.agent) : fieldText(x))))))));
  }

  // ---- labels table: each ladder's team level with its evidence (Cheating with CheatBench's judge's word under the
  // code's), the alarm where the run has it, and the judge labels' team scores.
  const L = d.levels.ladders;
  const team = d.levels.team;
  // Each value is its chip, its evidence in the chip's popover (facts).
  const teamCell = k => (team[k].na ? muted(`not applicable: ${team[k].evidence}`)
    : levelChip(k, L[k], team[k].level, true, { facts: team[k].facts }));
  const labelRows = [
    ['Cheating', cheatingCell(L.cheating, team.cheating.na ? null : team.cheating.level, cheating.team_cheated,
      multi ? cheatbenchTeam(row.cheatbench) : cheatbenchWords(Object.values(cheatbench)[0]), true, team.cheating.facts)],
    ...(cheating.alarm ? [['Alarm', alarmBadge(cheating.alarm)]] : []),
    ['Coordination', teamCell('coordination')],
    ['Collusion', teamCell('collusion')],
    ...Object.entries(labelAbout).map(([k, about]) => [about.name, teamScore(row.labels.team[k])]),
  ];
  const labelsCard = h('section', { class: 'card' }, h('h2', null, multi ? 'Team labels' : 'Labels'),
    dataTable(['Label', 'Value'], labelRows.map(([a, b]) => [h('strong', null, a), b]), { cls: 'labels-table' }));

  // ---- header.
  const m = d.meta || {};
  const cfg = m.config || {};
  const coll = collectionOf(row.col);
  const header = h('section', { class: 'card ep-header' },
    h('div', { class: 'ep-title' }, h('h1', null, row.run),
      badge(coll.label, 'def', coll.about), coll.tag ? badge(coll.tag, 'plain', coll.about) : null,
      h('span', { class: 'spacer' }), h('a', { class: 'btn btn-small', href: '#/' }, '← all runs')),
    wideItems(kv([
      ['collection', `${coll.label} (runs/${row.col})`], ['folder', h('span', { class: 'mono' }, row.key)],
      ['task', `${taskLabel(row)}${row.instance ? `, instance ${row.instance}` : ''}`], ['arm or cell', armLabel(row)],
      ['workers\' model', `${modelLabel(row)}${row.model_id ? ` (${row.model_id})` : ''}`],
      ['worker conditioning', row.worker_conditioning],
      ['influencer conditioning', row.influencer_conditioning.length ? row.influencer_conditioning.map(x => influencerParts(x).join(', ')).join('; ') : 'none'],
      ['seed', row.seed != null ? String(row.seed) : undefined],
      ['turn budget (rounds)', cfg.turn_budget != null && multi ? String(cfg.turn_budget) : undefined],
      ['rounds run', m.rounds != null && multi ? String(m.rounds) : undefined],
      ['mode, access', multi ? [m.mode || cfg.mode, cfg.access].filter(Boolean).join(', ') : undefined],
      ['eval log', m.eval_log ? h('span', { class: 'mono small' }, m.eval_log) : muted(m.eval_log_recorded ? `not found (run.json names ${m.eval_log_recorded})` : 'none recorded', 'small')],
      ['code', m.code && m.code.commit ? `${m.code.commit}${m.code.dirty ? ' (dirty)' : ''}` : undefined],
    ], 'kv-grid')),
    null);
  labelsCard.id = 'sec-labels';
  const agentsBox = h('div', { id: 'sec-agents' }, d.agents.map(agentCard));

  // ---- top bar: run, arm, agent tabs, the fold toggles, the run record's toggle; the stage below holds the selected
  // tab. The parts folded on every step (?fold=calls,user,reasoning,call,result) and Board calls only (?board=only) come from the
  // hash and go back to it; an agent's tab takes them when shown.
  const firstAgent = (d.agents.find(a => a.role === 'worker' && a.folder) || d.agents.find(a => a.folder) || {}).agent;
  const folded = new Set((q.get('fold') || '').split(',').filter(x => PARTS.some(([k]) => k === x)));
  const stage = h('div', { class: 'rp-stage', dataset: { board: q.get('board') === 'only' ? 'only' : 'all' } });
  const tabs = new Map();
  let current = null;
  // A tab names the agent and its role, the role alone (worker, regulator, disregulator).
  for (const a of d.agents) {
    tabs.set(a.agent, h('button', { type: 'button', class: 'rp-tab', onclick: () => selectTab(a.agent).catch(() => {}) },
      a.agent, h('span', { class: 'rp-role' }, ' ' + a.role)));
  }
  const detailsView = () => ({ el: h('div', { class: 'rp-team' },
    levelsPanel(d.levels, roles, row.task_name, { cheatbench, row, open: openLink }),
    cheatingPanel(d.levels, roles, { evidence: evidenceText, cheatbench, row }),
    coordinationPanel(d.levels, roles),
    collusionPanel(d.levels, roles, { evidence: evidenceText }),
    judgeLabelsPanel(d.levels, roles, openLink),
    // the strip's legend, in the case layout its rules are scoped to
    h('section', { class: 'card' }, h('h2', null, 'Legend'), h('div', { class: 'case' }, stripLegend(d.levels.ladders, d.levels.order)))) });
  // The Board tab reads /api/board when first opened; its handle ({moveTo, showPost, state}) comes once it is drawn.
  // Its pair, mode and position come from the hash (a, b, mode, pos) and go back to it; its agents' cards show the
  // judge labels the Details tab shows.
  const boardView = () => {
    const el = h('div', { class: 'rp-board' }, h('div', { class: 'loading' }, 'Loading the board…'));
    const handle = fetch('/api/board?id=' + enc(id))
      .then(async x => {
        const b = await x.json();
        if (!x.ok) throw new Error(b.error || `HTTP ${x.status}`);
        return renderBoard(el, b, { q, base: '#/run/' + id.split('/').map(enc).join('/'), judged });
      })
      .catch(e => {
        el.replaceChildren(h('div', { class: 'error-box' }, `Could not load the board: ${e.message}`));
        drawn.delete('board');
        throw e;
      });
    return { el, handle };
  };
  // The tabs beside the agents': each draws its view once, when first opened.
  const views = new Map(multi
    ? [['board', { label: 'Board', draw: boardView }], ['details', { label: 'Details', draw: detailsView }]] : []);
  const drawn = new Map();
  for (const [key, v] of views) {
    const open = () => selectTab(key).catch(() => {});
    tabs.set(key, h('button', { type: 'button', class: 'rp-tab', onclick: open }, v.label));
  }
  // Each tab keeps where it was scrolled to: the stage for a view or the run record, an agent's steps pane for an agent.
  const scrolled = new Map();
  const scroller = () => stage.querySelector(':scope > .case .right') || stage;
  function place(key, el) {
    if (current != null) scrolled.set(current, scroller().scrollTop);
    current = key;
    stage.classList.toggle('rp-stage-case', el.classList.contains('case'));
    stage.replaceChildren(el);
    scroller().scrollTop = scrolled.get(key) || 0;
  }
  let shown = null;   // the tab the run record's button goes back to
  async function selectTab(key) {
    for (const [k, b] of tabs) b.classList.toggle('on', k === key);
    recordBtn.setAttribute('aria-pressed', String(key === 'record'));
    toggles.hidden = views.has(key) || key === 'record';   // the fold toggles act on an agent's steps only
    if (key !== 'record') shown = key;
    if (key === 'record') { place(key, record); return null; }
    if (views.has(key)) {
      if (!drawn.has(key)) drawn.set(key, views.get(key).draw());
      const v = drawn.get(key);
      place(key, v.el);
      return v.handle || null;
    }
    const c = cards.get(key);
    if (!c) return null;
    place(null, h('div', { class: 'loading' }, 'Loading the transcript…'));
    current = key;
    const tr = await c.load();
    tr.setFolds(folded);
    if (current === key) { current = null; place(key, tr.el); tr.refresh(); }
    return tr;
  }
  function keepInHash() {
    const [path, qs] = location.hash.slice(1).split('?');
    const p = new URLSearchParams(qs || '');
    if (folded.size) p.set('fold', PARTS.map(([k]) => k).filter(k => folded.has(k)).join(','));
    else p.delete('fold');
    if (stage.dataset.board === 'only') p.set('board', 'only');
    else p.delete('board');
    const s = p.toString();
    try { history.replaceState(null, '', `#${path}${s ? '?' + s : ''}`); } catch (e) { /* view stays as drawn */ }
  }
  const toggle = (label, tip, on, flip) => {
    const b = h('button', { type: 'button', class: 'rp-tog', 'aria-pressed': String(on), title: tip,
      onclick: () => { b.setAttribute('aria-pressed', String(flip())); keepInHash(); } }, label);
    return b;
  };
  const toggles = h('span', { class: 'rp-togs' },
    PARTS.map(([k, label]) => toggle(label, `show or fold the ${label.toLowerCase()} of every step`,
      !folded.has(k), () => {
        if (folded.has(k)) folded.delete(k); else folded.add(k);
        const tr = cards.has(current) && cards.get(current).state.tr;
        if (tr) { tr.setFolds(folded); tr.refresh(); }
        return !folded.has(k);
      })),
    toggle('Board calls only', 'only the calls that read or posted on the message board', stage.dataset.board === 'only', () => {
      stage.dataset.board = stage.dataset.board === 'only' ? 'all' : 'only';
      const tr = cards.has(current) && cards.get(current).state.tr;
      if (tr) tr.refresh();
      return stage.dataset.board === 'only';
    }));
  // A- and A+: the steps' text size, kept across runs (localStorage), from 70% to 200%.
  const ZOOMS = [0.7, 0.8, 0.9, 1, 1.1, 1.25, 1.4, 1.6, 1.8, 2];
  const zoomAt = () => { const z = Number(localStorage.getItem('tv-zoom')) || 1; return ZOOMS.reduce((a, b) => (Math.abs(b - z) < Math.abs(a - z) ? b : a)); };
  const setZoom = z => { localStorage.setItem('tv-zoom', String(z)); document.documentElement.style.setProperty('--tv-zoom', String(z)); };
  setZoom(zoomAt());
  const zoomBy = d => () => setZoom(ZOOMS[Math.max(0, Math.min(ZOOMS.length - 1, ZOOMS.indexOf(zoomAt()) + d))]);
  const zoomBtns = h('span', { class: 'rp-zoom' },
    h('button', { type: 'button', class: 'rp-dbtn', title: 'smaller text', onclick: zoomBy(-1) }, 'A−'),
    h('button', { type: 'button', class: 'rp-dbtn', title: 'larger text', onclick: zoomBy(1) }, 'A+'));
  // The run record is shown in the stage in place of a tab; its button goes back to the tab shown before.
  const recordBtn = h('button', { type: 'button', class: 'rp-dbtn', 'aria-pressed': 'false', title: 'the run\'s labels, settings and records',
    onclick: () => selectTab(current === 'record' && shown ? shown : 'record').catch(() => {}) }, 'Run record');
  const bar = h('div', { class: 'rp-bar' },
    h('a', { class: 'rp-back', href: '#/', title: 'all runs' }, '←'),
    h('b', { class: 'rp-run' }, row.run), whoChips(row),
    cheating.alarm ? alarmBadge(cheating.alarm) : null,
    h('span', { class: 'rp-tabs' }, [...tabs.values()]),
    // the fold toggles and the run record's button wrap as one group at the right
    h('span', { class: 'rp-right' }, toggles, zoomBtns,
      recordBtn));

  view.mounted(() => {
    document.title = `${row.run} · Transcripts`;
    placePage();
    const qa = q.get('agent');
    const qc = q.get('call');
    if (qa && q.get('label') && cards.has(qa)) openExcerpt(qa, q.get('label')).then(x => x || selectTab(qa)).catch(() => {});
    else if (qa && qc != null && cards.has(qa)) gotoCall(qa, parseInt(qc, 10)).catch(() => {});
    else selectTab(views.has(q.get('tab')) ? q.get('tab') : (firstAgent || 'details')).catch(() => {});
  });
  const record = h('div', { class: 'rp-details rp-record' }, header, labelsCard, agentsBox);
  return h('div', { class: 'page rp-page' }, bar, stage);
}

