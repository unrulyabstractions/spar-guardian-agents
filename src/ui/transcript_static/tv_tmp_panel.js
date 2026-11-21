/* The Board tab's /tmp panel: each machine's temporary directories (/tmp, /var/tmp, /dev/shm) at the position, from the
 * run's tmp_snapshots.json (/api/tmp?id=; the state by tv_tmp_state.js). Shown only when the board payload says the
 * run has the file (BoardTab.tmp).
 *
 *   const panel = tmpPanel(url, { timeOf, callHref });
 *   panel.el;                    // the card, to place in the tab
 *   panel.pair(a, b);            // the two agents in the lanes, whose names take the lanes' colours
 *   panel.at(t, tPrev);          // the position: the state at t, and what changed after tPrev up to t
 *
 * A machine's tree lists every entry, a directory before its entries and foldable by a click; an entry changed in the
 * window is marked added, changed or removed, with the call that changed it. Each row gives the entry's owner and a
 * file's size. A click on a file shows its text, when the snapshot captured it, beside the trees. The panel carries
 * no sentence: labels, values, chips and short hovers. */
import { enc, h, plural } from '/static/app/dom.js';
import { muted } from '/static/app/components.js';
import { changesIn, countAt, stateAt, tmpTimeline, treeRows } from './tv_tmp_state.js';

const MARK = { added: '+', changed: '~', removed: '−' };
const KIND_TIP = { added: 'added at this step', changed: 'changed at this step', removed: 'removed at this step' };

const fmtBytes = n => (n == null ? '' : n < 1024 ? `${n} B` : n < 1 << 20 ? `${(n / 1024).toFixed(1)} KiB`
  : `${(n / (1 << 20)).toFixed(1)} MiB`);

export function tmpPanel(url, opts) {
  const head = h('div', { class: 'tmp-head' }, h('h3', { class: 'bd-board-head' }, '/tmp'));
  const counts = h('span', { class: 'tmp-counts' });
  head.appendChild(counts);
  const trees = h('div', { class: 'tmp-trees' }, muted('loading', 'small'));
  const file = h('div', { class: 'tmp-file', hidden: true });
  const el = h('section', { class: 'card bd-tmp' }, head, h('div', { class: 'tmp-body' }, trees, file));
  let tl = null;
  let failed = false;
  let pair = { a: null, b: null };
  let drawn = null;        // [c0, c1] last drawn
  let at = [0, -Infinity];
  let selected = null;     // {machine, path}
  const folded = new Set();

  fetch(url).then(async r => {
    const doc = await r.json();
    if (!r.ok) throw new Error(doc.error || `HTTP ${r.status}`);
    tl = tmpTimeline(doc, opts.timeOf);
    draw(true);
  }).catch(e => {
    failed = true;
    trees.replaceChildren(h('span', { class: 'error-box small' }, `/tmp: ${e.message}`));
  });

  const sideOf = agent => (agent && agent === pair.a ? 'a' : agent && agent === pair.b ? 'b' : null);
  const agentName = (agent, cls) => {
    const side = sideOf(agent);
    return h('span', { class: ['tmp-agent', side ? `bd-key-${side}` : agent === 'root' ? 'muted' : null, cls] },
      agent);
  };
  const callLink = by => h('a', { class: 'bd-call-link tmp-by', href: opts.callHref(by.agent, by.k),
    title: `open call ${by.k} in ${by.agent}'s tab` }, agentName(by.agent), ` call ${by.k}`);

  function draw(force) {
    if (!tl || failed) return;
    const c1 = countAt(tl, at[0]);
    const c0 = Math.min(c1, countAt(tl, at[1]));
    if (!force && drawn && drawn[0] === c0 && drawn[1] === c1) return;
    drawn = [c0, c1];
    const now = stateAt(tl, c1);
    const before = stateAt(tl, c0);
    const changes = changesIn(tl, c0, c1);
    const total = { added: 0, changed: 0, removed: 0 };
    for (const m of tl.machines) for (const c of Object.values(changes[m] || {})) total[c.kind] += 1;
    trees.replaceChildren(...tl.machines.map(m => machineTree(m, now[m], before[m], changes[m], c1)));
    counts.replaceChildren(...Object.entries(total).filter(([, n]) => n).map(([kind, n]) =>
      h('span', { class: ['bd-count', `tmp-c-${kind}`], title: KIND_TIP[kind] }, `${MARK[kind]}${n}`)));
    showFile(now, before, changes);
  }

  // The last step that ran in the machine among the first c1, or null.
  const lastIn = (m, c1) => {
    for (let i = c1 - 1; i >= 0; i -= 1) if (tl.steps[i].machine === m) return tl.steps[i];
    return null;
  };

  function machineTree(m, now, before, changes, c1) {
    const rows = treeRows(tl.dirs, now, changes, before);
    const last = lastIn(m, c1);
    const agents = tl.agents[m] || [];
    const mHead = h('div', { class: 'tmp-mhead' }, h('b', { class: 'tmp-machine' }, agentName(m)),
      agents.length > 1 ? h('span', { class: 'bd-count', title: agents.join(' ') }, plural(agents.length, 'agent'))
        : null,
      h('span', { class: 'bd-count', title: 'entries now' }, plural(Object.keys(now || {}).length, 'entry', 'entries')),
      h('span', { class: 'spacer' }),
      last ? h('span', { class: 'small muted', title: 'last call run in this machine' }, 'after ', callLink(last))
        : h('span', { class: 'bd-count', title: 'before the machine\'s first call' }, 'baseline'));
    const list = h('div', { class: 'tmp-rows', role: 'tree' });
    let hideUnder = null;
    for (const r of rows) {
      if (hideUnder && r.path.startsWith(hideUnder + '/')) continue;
      hideUnder = null;
      const e = r.entry || {};
      const dir = e.type === 'dir';
      const key = `${m}\n${r.path}`;
      const isFolded = dir && folded.has(key);
      if (isFolded) hideUnder = r.path;
      const sel = selected && selected.machine === m && selected.path === r.path;
      const name = dir ? `${r.name}${r.depth === 0 ? '' : '/'}` : e.type === 'link' ? `${r.name} → ${e.target}` : r.name;
      const row = h('div', { class: ['tmp-row', `tmp-t-${e.type || 'other'}`, r.change && `tmp-${r.change.kind}`,
        sel && 'tmp-sel'], role: 'treeitem', title: [e.mode, e.owner, dir ? null : fmtBytes(e.size)].filter(Boolean)
        .join(' · ') },
      h('span', { class: 'tmp-mark', title: r.change ? KIND_TIP[r.change.kind] : null },
        r.change ? MARK[r.change.kind] : ''),
      h('span', { class: 'tmp-name', style: `padding-left:${r.depth * 14}px` },
        dir ? h('span', { class: 'tmp-fold' }, isFolded ? '▸' : '▾') : h('span', { class: 'tmp-fold' }), name),
      agentName(e.owner || '', 'tmp-owner'),
      h('span', { class: 'tmp-size' }, dir ? '' : fmtBytes(e.size)),
      h('span', { class: 'tmp-by-cell' }, r.change ? (r.change.by ? callLink(r.change.by)
        : muted('between calls', 'small')) : null));
      row.addEventListener('click', ev => {
        if (ev.target.closest('a')) return;
        if (dir) {
          if (folded.has(key)) folded.delete(key); else folded.add(key);
        } else selected = sel ? null : { machine: m, path: r.path };
        draw(true);
      });
      list.appendChild(row);
    }
    return h('div', { class: 'tmp-machine-box' }, mHead, list);
  }

  // The selected file's text at the position: as it is now, or as it was when removed in the window.
  function showFile(now, before, changes) {
    file.hidden = !selected;
    if (!selected) return;
    const { machine: m, path } = selected;
    const change = (changes[m] || {})[path] || null;
    const e = (now[m] || {})[path] || (change && change.kind === 'removed' ? (before[m] || {})[path] : null);
    const close = h('button', { type: 'button', class: 'btn btn-small tmp-close', title: 'close',
      onclick: () => { selected = null; draw(true); } }, '×');
    const chips = e ? [agentName(e.owner || '', 'tmp-owner'), h('span', { class: 'bd-count' }, fmtBytes(e.size)),
      h('span', { class: 'bd-count tmp-mode' }, e.mode),
      e.sha256 ? h('span', { class: 'bd-count tmp-mode', title: e.sha256 }, e.sha256.slice(0, 12)) : null] : [];
    const state = change ? h('span', { class: ['bd-count', `tmp-c-${change.kind}`] }, change.kind) : null;
    const body = !e ? muted('absent', 'small block')
      : e.text != null ? h('pre', { class: 'pre tmp-text' }, e.text)
        : muted('text not captured', 'small block');
    file.replaceChildren(h('div', { class: 'tmp-fhead' }, h('b', { class: 'tmp-path' }, path),
      tl.machines.length > 1 ? muted(m, 'small') : null, state, ...chips, h('span', { class: 'spacer' }), close),
    body);
  }

  return {
    el,
    pair(a, b) { pair = { a, b }; draw(true); },
    at(t, tPrev) { at = [t, tPrev]; draw(false); },
  };
}

export const tmpUrl = id => `/api/tmp?id=${enc(id)}`;
