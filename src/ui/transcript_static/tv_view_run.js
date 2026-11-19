/* View: one run (#/run/<collection>/<run key>?agent=<id>&call=<k>&tab=team). Ported from collusion-hermaneutics'
 * viewer (viewer/static/app/view_run.js). A bar with the run, an agent tab per agent and a Team tab; the selected
 * agent's whole episode below it, each step with its labels (tv_case_view.js); the details under it: the run's
 * labels, the run settings, each agent's record with its Cheating evidence and CheatBench's judge on it.
 * Our edits: the imports (dom.js, components.js and charts.js are the results browser's own); the judges are read
 * from the run's verdict files (judges/<pass>.<judge>.jsonl), shown where the run has them; the source's API judges,
 * second judge and sentence lens are gone; a disregulator is a hidden role like a regulator; the header names the
 * collection and the eval log; the Team tab holds the Cheating, Coordination and Collusion panels. A tab beside the
 * agents' is one entry of `views` (a key, its label and a function that draws it). */
import { appendAll, enc, fmtInt, fmtUsd, h, plural } from '/static/app/dom.js';
import { badge, dataTable, episodeRecord, kv, lazyDetails, longText, muted } from '/static/app/components.js';
import { ROLE_ORDER, roleIcon } from '/static/app/charts.js';
import { CHEATBENCH, DISCLOSURE, ROLE_LABEL, armLabel, collectionOf, modelLabel, taskLabel } from './tv_labels.js';
import { cheatbenchTeam, cheatbenchWords, cheatedTag, cheatingPanel, clauseList, collusionPanel, coordinationPanel,
  disclosureTeam, judgeLabels, levelChip, levelsPanel } from './tv_levels.js';
import { fullLegend, renderCase } from './tv_case_view.js';

function flash(el) {
  if (!el) return;
  el.scrollIntoView({ block: 'center' });
  el.classList.remove('flash');
  void el.offsetWidth;
  el.classList.add('flash');
}

function roleTag(role) {
  return h('span', { class: ['badge', 'b-plain', 'role-tag', 'role-' + role], title: 'the agent\'s role in the run' },
    roleIcon(ROLE_ORDER.includes(role) ? role : 'worker', 10), ' ', ROLE_LABEL[role] || role);
}

const fieldText = v => (v === true ? 'yes' : v === false ? 'no' : typeof v === 'string' ? v : JSON.stringify(v));

export async function viewRun(id, q, view) {
  const r = await fetch('/api/run?id=' + enc(id));
  const d = await r.json();
  if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
  const row = d.row;
  const agentIds = d.agents.map(a => a.agent);
  const roles = Object.fromEntries(d.agents.map(a => [a.agent, a.role]));
  const multi = d.agents.length > 1;

  // ---- board: post numbers, and the posts the honeypot scan marks as naming the exploit (the Collusion ladder's
  // exploit-named evidence, src/measures/collusion.py naming_posts).
  const posts = new Map();
  for (const b of d.boards) for (const m of b.messages) if (m.post_n != null) posts.set(m.post_n, m);
  const collusion = (d.levels && d.levels.collusion) || {};
  const cheating = (d.levels && d.levels.cheating) || {};
  const namingByPost = new Map((collusion.naming_posts || []).filter(p => p.post_n != null).map(p => [p.post_n, p]));

  // Calls that read a hidden regulator's post (a worker), or that posted as the regulator (the regulator).
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
    const btn = h('button', { type: 'button', class: 'btn', disabled: !a.folder }, a.folder ? `Show transcript (${sizeTxt})` : 'No transcript on disk');
    const load = () => {
      if (!state.loaded) {
        btn.disabled = true;
        btn.textContent = 'Loading transcript…';
        state.loaded = fetch(`/api/transcript?id=${enc(id)}&agent=${enc(a.agent)}`)
          .then(x => { if (!x.ok) throw new Error(`HTTP ${x.status}`); return x.json(); })
          .then(t => {
            state.tr = renderCase(t, { agent: a.agent, levels: d.levels, isRegulator: a.role === 'regulator', regulatorCalls: regulatorCalls(a.agent, a.role) });
            holder.replaceChildren(muted(`The transcript of ${a.agent} is shown at the top of the page, under its tab.`, 'small block'));
            btn.textContent = `Transcript at the top (${sizeTxt})`;
            return state.tr;
          })
          .catch(e => { holder.replaceChildren(h('div', { class: 'error-box' }, `Could not load the transcript: ${e.message}`)); btn.disabled = false; btn.textContent = 'Retry loading the transcript'; state.loaded = null; throw e; });
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
      episodeRecord(Object.assign({}, a.episode, { role: undefined })),
      agentCheating(a.agent),
      h('div', null, h('h3', { class: 'sub-h' }, CHEATBENCH.column), cheatbenchBlock(a)),
      a.episode.final_text ? lazyDetails('final text of the agent', () => longText(a.episode.final_text, { lines: 40 }), { cls: 'sub' }) : null,
      holder]);
    cards.set(a.agent, { card, load, state });
    return card;
  }

  async function gotoCall(agent, k) {
    const c = cards.get(agent);
    if (!c) return;
    const tr = await selectTab(agent);
    tr.showAll();
    const box = tr.findCall(k);
    if (box) flash(box);
    else flash(c.card);
  }
  async function gotoToolCall(agent, callId) {
    const c = cards.get(agent);
    if (!c) return;
    const tr = await selectTab(agent);
    tr.showAll();
    flash(tr.findToolCall(callId) || c.card);
  }
  const gotoPost = n => flash(document.getElementById(`post-${n}`));
  // The collusion judge names a post by its place on the board, P1, P2, ..., system notices included (board_no).
  const boardNos = new Map(d.boards.flatMap(b => b.messages.map(m => [`P${m.board_no}`, m])));
  const boardPost = label => {
    const m = boardNos.get(label);
    if (!m) return h('span', { class: 'mono' }, label);
    const id = m.post_n != null ? `post-${m.post_n}` : `board-${m.board_no}`;
    return h('button', { type: 'button', class: 'linkish ev-link', title: 'show this post on the board', onclick: () => flash(document.getElementById(id)) },
      `${label}${m.post_n != null ? ` (post ${m.post_n} by ${m.sender})` : ' (a system notice)'}`);
  };

  // Evidence text with every [k] linked to call k of the agent it names, and every "post N" to the board.
  function evidenceText(text, agent) {
    const s = String(text == null ? '' : text);
    let who = agent;
    const lead = /^([A-Za-z0-9_-]+): /.exec(s);
    if (lead && agentIds.includes(lead[1])) who = lead[1];
    const out = [];
    let byRegulator = false;
    let last = 0;
    const re = /\[(\d+)\]|\bpost (\d+)\b/g;
    let m;
    while ((m = re.exec(s))) {
      if (m.index > last) out.push(s.slice(last, m.index));
      if (m[1] != null && who && agentIds.includes(who)) {
        const k = parseInt(m[1], 10);
        out.push(h('button', { type: 'button', class: 'linkish ev-link', title: `open call ${k} in ${who}'s transcript`, onclick: () => gotoCall(who, k) }, m[0]));
      } else if (m[2] != null && posts.has(parseInt(m[2], 10))) {
        const n = parseInt(m[2], 10);
        const p = posts.get(n);
        out.push(h('button', { type: 'button', class: 'linkish ev-link', title: 'show this post on the board', onclick: () => gotoPost(n) }, m[0]));
        if (p.role === 'regulator') byRegulator = true;
      } else out.push(m[0]);
      last = re.lastIndex;
    }
    if (last < s.length) out.push(s.slice(last));
    if (byRegulator) out.push(' ', badge('written by the hidden regulator', 'notice', 'the post this evidence cites was written by the run\'s hidden regulator'));
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
    return h('div', null, h('h3', { class: 'sub-h' }, 'Cheating for this agent'),
      h('div', { class: 'verdict-flags' }, levelChip('cheating', L, r.rung), ' ', cheatedTag(r.cheated),
        muted(` ${L.glosses[L.levels.indexOf(r.rung)]}`, 'small')),
      clauseList(d.levels, r.clauses, agent, { evidence: evidenceText }),
      (r.below || []).length ? h('div', { class: 'small' }, h('span', { class: 'field-label' }, 'Below the ladder, evidence only: '),
        evidenceList(r.below, agent)) : null,
      h('div', { class: 'small' }, h('span', { class: 'field-label' }, 'CheatBench\'s judge\'s labels beside the code: '), judgeLabels(r)));
  }

  function cheatbenchBlock(a) {
    const judges = Object.entries(a.cheatbench || {});
    if (!judges.length) return muted('Not judged: no CheatBench verdict file holds this agent.', 'small block');
    return judges.map(([judge, v]) => h('div', { class: 'judge-block' },
      h('div', { class: 'jb-head' }, h('h4', null, `judge ${judge}`), cheatbenchWords({ [judge]: v })),
      v.summary ? h('p', { class: 'verdict-summary' }, evidenceText(v.summary, a.agent)) : null,
      h('dl', { class: 'kv kv-grid' }, Object.entries(v).filter(([k]) => !['agent', 'summary'].includes(k)).map(([k, x]) =>
        h('div', { class: 'kv-item' }, h('dt', null, k.replace(/_/g, ' ')),
          h('dd', null, k === 'judge_cost_usd' ? fmtUsd(x) : typeof x === 'string' ? evidenceText(x, a.agent) : fieldText(x)))))));
  }

  // ---- labels table: each ladder's team level, the alarm and the judges.
  const L = d.levels.ladders;
  const teamCell = k => {
    const c = d.levels.team[k];
    return c.na ? muted(`not applicable: ${c.evidence}`) : h('span', null, h('b', null, c.level), muted(` (${c.index} of ${c.of}) ${c.evidence}`, 'small'));
  };
  const labelRows = [
    ['Cheating (team)', teamCell('cheating'), L.cheating.rule],
    ['Cheated (team)', cheatedTag(cheating.team_cheated) || muted('not applicable'), L.cheating.line_words],
    ['Alarm', cheating.alarm ? badge(`alarm: ${cheating.alarm}`, 'bad') : muted('none'), L.cheating.note],
    ['Coordination (team)', teamCell('coordination'), L.coordination.rule],
    ['Collusion (team)', teamCell('collusion'), L.collusion.rule],
    [CHEATBENCH.column, cheatbenchTeam(row.cheatbench), CHEATBENCH.about],
    [DISCLOSURE.column, disclosureTeam(row.disclosure), DISCLOSURE.about],
    ['Collusion judge', collusion.judged ? `verdicts of ${collusion.judge}` : muted('not judged'),
      'The collusion judge reads the collaborating pairs; its verdicts give the Collusion ladder\'s two judge rungs.'],
  ];
  const labelsCard = h('section', { class: 'card' }, h('h2', null, 'Labels for this run'),
    dataTable(['Label', 'Value', 'What it means'], labelRows.map(([a, b, c]) => [h('strong', null, a), b, h('span', { class: 'small' }, c)]), { cls: 'labels-table' }));

  // ---- board.
  let boardCard = null;
  if (multi) {
    boardCard = h('section', { class: 'card' }, h('h2', null, 'Message board'),
      !d.boards.length ? muted('No board file: nobody posted.') : d.boards.map(b => {
        const nPosts = b.messages.filter(m => m.kind === 'post').length;
        return h('div', { class: 'board' },
          h('div', { class: 'board-head' }, muted(`${plural(nPosts, 'post')} by agents, ${plural(b.messages.length - nPosts, 'system notice')}; board events: ` +
            Object.entries(b.event_counts).map(([k, n]) => `${k} ${n}`).join(', '))),
          h('ol', { class: 'board-msgs' }, b.messages.map(m => {
            const isSys = m.kind !== 'post';
            const naming = m.post_n != null ? namingByPost.get(m.post_n) : null;
            return h('li', { class: ['board-msg', isSys && 'bm-system', m.role && m.role !== 'worker' && 'bm-regulator'], id: m.post_n != null ? `post-${m.post_n}` : `board-${m.board_no}` },
              h('div', { class: 'bm-head' },
                h('span', { class: 'bm-seq', title: 'the number the agents see on the board, system posts included' }, `#${m.board_no}`),
                m.post_n != null ? badge(`post ${m.post_n}`, 'plain', 'the number the evidence cites ("post N"), agents\' posts only') : null,
                isSys ? badge(m.kind === 'submit' ? 'system notice: hand-in' : 'system notice', 'notice', 'written by the harness, not by an agent') : null,
                h('span', { class: 'bm-sender' }, m.sender || '?'),
                m.role && m.role !== 'worker' ? roleTag(m.role) : null,
                muted(` → ${(m.recipients || []).join(', ') || 'all'}`),
                m.round != null ? muted(` · round ${m.round}${m.round_step && /free/.test(m.round_step) ? ' (free board action)' : ''}`) : null,
                naming ? badge('names the exploit', 'warn', `the honeypot scan marks this post as naming the planted material (${naming.kinds.join(', ')}); read by ${naming.readers.join(', ') || 'no teammate'}. Evidence for the Collusion ladder's exploit-named flag, which a judge would read.`) : null,
                h('span', { class: 'spacer' }),
                m.sent && agentIds.includes(m.sent.agent) ? h('button', { type: 'button', class: 'linkish small', onclick: () => gotoToolCall(m.sent.agent, m.sent.call_id) }, 'show the posting call') : null),
              longText(m.content, { lines: 25, cls: 'msg-text' }),
              m.reads.length ? h('div', { class: 'small muted' }, 'read by: ', [...new Set(m.reads.map(x => x.agent))].map((ag, i) => {
                const first = m.reads.find(x => x.agent === ag);
                return [i ? ', ' : '', agentIds.includes(ag) ? h('button', { type: 'button', class: 'linkish', onclick: () => gotoToolCall(ag, first.call_id) }, ag) : ag];
              })) : null);
          })));
      }));
  }

  // ---- header.
  const m = d.meta || {};
  const cfg = m.config || {};
  const coll = collectionOf(row.col);
  const hidden = r => Object.values(roles).filter(x => x === r).length;
  const header = h('section', { class: 'card ep-header' },
    h('div', { class: 'ep-title' }, h('h1', null, row.run),
      badge(coll.label, 'def', coll.about), coll.tag ? badge(coll.tag, 'plain', coll.about) : null,
      h('span', { class: 'spacer' }), h('a', { class: 'btn btn-small', href: '#/' }, '← all runs')),
    kv([
      ['collection', `${coll.label} (runs/${row.col})`], ['folder', h('span', { class: 'mono' }, row.key)],
      ['task', `${taskLabel(row)}${row.instance ? `, instance ${row.instance}` : ''}`], ['arm or cell', armLabel(row)],
      ['seed post', row.seed_post || undefined], ['workers\' model', `${modelLabel(row)}${row.model_id ? ` (${row.model_id})` : ''}`],
      ['agents', `${row.n_agents}${hidden('regulator') ? ` (${hidden('regulator')} hidden regulator)` : ''}${hidden('disregulator') ? ` (${hidden('disregulator')} hidden disregulator)` : ''}`],
      ['seed', row.seed != null ? String(row.seed) : undefined],
      ['turn budget (rounds)', cfg.turn_budget != null && multi ? String(cfg.turn_budget) : undefined],
      ['rounds run', m.rounds != null && multi ? String(m.rounds) : undefined],
      ['mode, access', multi ? [m.mode || cfg.mode, cfg.access].filter(Boolean).join(', ') : undefined],
      ['eval log', m.eval_log ? h('span', { class: 'mono small' }, m.eval_log) : muted(m.eval_log_recorded ? `not found (run.json names ${m.eval_log_recorded})` : 'none recorded', 'small')],
      ['code', m.code && m.code.commit ? `${m.code.commit}${m.code.dirty ? ' (dirty)' : ''}` : undefined],
    ], 'kv-grid'),
    null);
  labelsCard.id = 'sec-labels';
  if (boardCard) boardCard.id = 'sec-board';
  const agentsBox = h('div', { id: 'sec-agents' }, d.agents.map(agentCard));

  // ---- top bar: run, arm, agent tabs, Show filter, Details toggle; the stage below holds the selected tab.
  const firstAgent = (d.agents.find(a => a.role === 'worker' && a.folder) || d.agents.find(a => a.folder) || {}).agent;
  const stage = h('div', { class: 'rp-stage', dataset: { show: 'all' } });
  const tabs = new Map();
  let current = null;
  const ROLE_WORD = ROLE_LABEL;
  for (const a of d.agents) {
    tabs.set(a.agent, h('button', { type: 'button', class: 'rp-tab', onclick: () => selectTab(a.agent).catch(() => {}) },
      a.agent, h('span', { class: 'rp-role' }, ' ' + (ROLE_WORD[a.role] || a.role))));
  }
  const linkPost = n => h('button', { type: 'button', class: 'linkish ev-link', title: 'show this post on the board', onclick: () => gotoPost(n) }, `post ${n}`);
  const cheatbench = Object.fromEntries(d.agents.map(a => [a.agent, a.cheatbench]));
  const judged = { disclosure: d.disclosure, teamDisclosure: row.disclosure };
  const teamView = () => h('div', { class: 'rp-team' },
    levelsPanel(d.levels, roles, row.task_name, { cheatbench, team: row.cheatbench, ...judged }),
    cheatingPanel(d.levels, roles, { evidence: evidenceText }),
    coordinationPanel(d.levels, roles),
    collusionPanel(d.levels, roles, { evidence: evidenceText, post: linkPost, boardPost, judges: d.collusion_judges, ...judged }),
    h('section', { class: 'card' }, h('h2', null, 'Colour legend'), fullLegend(d.levels.ladders, d.levels.order)), boardCard);
  // The tabs beside the agents': each draws its view once, when first opened.
  const views = new Map(multi ? [['team', { label: 'Team', draw: teamView }]] : []);
  const drawn = new Map();
  for (const [key, v] of views) tabs.set(key, h('button', { type: 'button', class: 'rp-tab', onclick: () => selectTab(key) }, v.label));
  async function selectTab(key) {
    current = key;
    for (const [k, b] of tabs) b.classList.toggle('on', k === key);
    if (views.has(key)) {
      if (!drawn.has(key)) drawn.set(key, views.get(key).draw());
      stage.replaceChildren(drawn.get(key));
      return null;
    }
    const c = cards.get(key);
    if (!c) return null;
    stage.replaceChildren(h('div', { class: 'loading' }, 'Loading the transcript…'));
    const tr = await c.load();
    if (current === key) stage.replaceChildren(tr.el);
    return tr;
  }
  const showSel = h('select', { 'aria-label': 'Which text to show', onchange: e => { stage.dataset.show = e.target.value; } },
    [['all', 'All'], ['reasoning', 'Reasoning'], ['tools', 'Tool calls'], ['board', 'Board calls']].map(([v, t]) => h('option', { value: v }, t)));
  const bar = h('div', { class: 'rp-bar' },
    h('a', { class: 'rp-back', href: '#/', title: 'all runs' }, '←'),
    h('b', { class: 'rp-run' }, row.run), h('span', { class: 'rp-arm' }, armLabel(row)),
    cheating.alarm ? badge(`alarm: ${cheating.alarm}`, 'bad', d.levels.ladders.cheating.note) : null,
    h('span', { class: 'rp-tabs' }, [...tabs.values()]),
    h('label', { class: 'rp-show' }, 'Show ', showSel),
    h('button', { type: 'button', class: 'rp-dbtn', onclick: () => { details.open = !details.open; if (details.open) details.scrollIntoView({ block: 'start' }); } }, 'Details'));

  view.mounted(() => {
    document.title = `${row.run} · Transcripts`;
    const qa = q.get('agent');
    const qc = q.get('call');
    if (qa && qc != null && cards.has(qa)) gotoCall(qa, parseInt(qc, 10)).catch(() => {});
    else selectTab(views.has(q.get('tab')) ? q.get('tab') : (firstAgent || 'team')).catch(() => {});
  });
  const details = h('details', { class: 'rp-details' }, h('summary', null, 'Details: labels, run settings, each agent\'s record with its Cheating evidence and the judges'),
    header, labelsCard, agentsBox);
  return h('div', { class: 'page rp-page' }, bar, stage, details);
}

