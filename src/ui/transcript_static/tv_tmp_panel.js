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
 * window is marked added, changed or removed, with the call that changed it; a path removed earlier stays as a faint
 * row (a chip in the machine's head hides them). Each row gives the entry's owner, the agents who can read it
 * (readersOf: mode, group and POSIX ACL; the lanes' two in their colours) and a file's size; `+` after a mode marks
 * an ACL, as `ls -l` does. A click on a file shows its text, when the snapshot captured it, beside the trees. The
 * panel carries no sentence: labels, values, chips and short hovers. */
import { enc, h, plural } from '/static/app/dom.js';
import { muted } from '/static/app/components.js';
import { withTipAbove } from '/static/app/charts.js';
import { agentUsers, changesIn, countAt, goneAt, readersOf, stateAt, tmpTimeline, treeRows } from './tv_tmp_state.js';

const MARK = { added: '+', changed: '~', removed: '−' };
const KIND_TIP = { added: 'added since the previous stop', changed: 'changed since the previous stop',
  removed: 'removed since the previous stop' };
const GONE_TIP = 'removed before the previous stop';
const READERS_SHOWN = 2;
const modeText = e => (e.mode ? `${e.mode}${e.acl ? '+' : ''}` : '');

const fmtBytes = n => (n == null ? '' : n < 1024 ? `${n} B` : n < 1 << 20 ? `${(n / 1024).toFixed(1)} KiB`
  : `${(n / (1 << 20)).toFixed(1)} MiB`);

export function tmpPanel(url, opts) {
  const head = h('div', { class: 'tmp-head' }, h('h3', { class: 'bd-board-head', title: 'Each machine\'s listed folders' },
    'Files'));
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
  const hideGone = new Set();   // machines whose earlier removed paths are hidden

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
  // Who can read: no agent, every agent, or the names (the first READERS_SHOWN, then +n).
  const readersChip = (names, all) => {
    const tip = names.length ? `read by ${names.join(' ')}` : 'no agent can read';
    if (!names.length) return h('span', { class: 'tmp-readers muted', title: tip }, '—');
    if (all > 1 && names.length === all) return h('span', { class: 'tmp-readers', title: tip }, h('span',
      { class: 'bd-count' }, 'all'));
    const shown = names.slice(0, READERS_SHOWN).flatMap((n, i) => (i ? [' ', agentName(n)] : [agentName(n)]));
    const more = names.length - READERS_SHOWN;
    return h('span', { class: 'tmp-readers', title: tip }, ...shown, more > 0 ? h('span', { class: 'muted' },
      ` +${more}`) : null);
  };
  // The machine's name: an agent's own machine in its colour, a shared one plain.
  const machineName = m => h('b', { class: 'tmp-machine' }, (tl.agents[m] || []).length === 1 && tl.agents[m][0] === m
    ? agentName(m) : h('span', { class: 'tmp-machine-name' }, m));

  function draw(force) {
    if (!tl || failed) return;
    const c1 = countAt(tl, at[0]);
    const c0 = Math.min(c1, countAt(tl, at[1]));
    if (!force && drawn && drawn[0] === c0 && drawn[1] === c1) return;
    drawn = [c0, c1];
    const now = stateAt(tl, c1);
    const before = stateAt(tl, c0);
    const changes = changesIn(tl, c0, c1);
    const gone = goneAt(tl, c1);
    const total = { added: 0, changed: 0, removed: 0 };
    for (const m of tl.machines) for (const c of Object.values(changes[m] || {})) total[c.kind] += 1;
    trees.replaceChildren(...tl.machines.map(m => machineTree(m, now[m], before[m], changes[m], gone[m], c1)));
    counts.replaceChildren(...Object.entries(total).filter(([, n]) => n).map(([kind, n]) =>
      h('span', { class: ['bd-count', `tmp-c-${kind}`], title: KIND_TIP[kind] }, `${MARK[kind]}${n}`)));
    showFile(now, before, changes, gone);
  }

  // The last step that ran in the machine among the first c1, or null.
  const lastIn = (m, c1) => {
    for (let i = c1 - 1; i >= 0; i -= 1) if (tl.steps[i].machine === m) return tl.steps[i];
    return null;
  };

  function machineTree(m, now, before, changes, gone, c1) {
    const earlier = Object.keys(gone || {}).filter(p => !(changes || {})[p]).length;
    const rows = treeRows(tl.dirs, now, changes, before, hideGone.has(m) ? {} : gone);
    const last = lastIn(m, c1);
    const agents = tl.agents[m] || [];
    const users = agentUsers(tl, m);
    const people = Object.keys(users).length;
    const goneChip = earlier ? h('button', { type: 'button', class: ['bd-count', 'tmp-gone-chip',
      hideGone.has(m) && 'tmp-off'], title: hideGone.has(m) ? 'show removed paths' : 'hide removed paths',
    onclick: () => { if (hideGone.has(m)) hideGone.delete(m); else hideGone.add(m); draw(true); } },
    `${earlier} removed`) : null;
    const mHead = h('div', { class: 'tmp-mhead' }, h('span', { class: 'small muted' }, 'machine'), machineName(m),
      agents.length > 1 ? h('span', { class: 'bd-count', title: agents.join(' ') }, plural(agents.length, 'agent'))
        : null,
      h('span', { class: 'bd-count', title: 'entries now' }, plural(Object.keys(now || {}).length, 'entry', 'entries')),
      goneChip,
      h('span', { class: 'spacer' }),
      last ? h('span', { class: 'small muted', title: 'last call run in this machine' }, 'after ', callLink(last))
        : h('span', { class: 'bd-count', title: 'before the machine\'s first call' }, 'baseline'));
    const list = h('div', { class: ['tmp-rows', people && 'tmp-has-readers'], role: 'tree' });
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
      const here = !r.gone && !(r.change && r.change.kind === 'removed');
      const owners = [e.owner, e.group].filter(Boolean).join(':');
      const by = r.change ? r.change.by : r.gone ? r.gone.by : null;
      // its mode, owner and size in a tip above the row, so it never covers the rows or the panel under it
      const row = withTipAbove(h('div', { class: ['tmp-row', `tmp-t-${e.type || 'other'}`, r.change && `tmp-${r.change.kind}`,
        r.gone && 'tmp-gone', sel && 'tmp-sel'], role: 'treeitem' },
      h('span', { class: 'tmp-mark', title: r.change ? KIND_TIP[r.change.kind] : r.gone ? GONE_TIP : null },
        r.change ? MARK[r.change.kind] : r.gone ? '×' : ''),
      h('span', { class: 'tmp-name', style: `padding-left:${r.depth * 14}px` },
        dir ? h('span', { class: 'tmp-fold' }, isFolded ? '▸' : '▾') : h('span', { class: 'tmp-fold' }), name),
      agentName(e.owner || '', 'tmp-owner'),
      people ? (here ? readersChip(readersOf(now, r.path, users), people) : h('span', { class: 'tmp-readers' }))
        : null,
      h('span', { class: 'tmp-size' }, dir ? '' : fmtBytes(e.size)),
      h('span', { class: 'tmp-by-cell' }, r.change || r.gone ? (by ? callLink(by)
        : muted('between calls', 'small')) : null)),
      () => [[modeText(e), owners, dir ? null : fmtBytes(e.size)].filter(Boolean).join(' · ')]);
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

  // The selected file's text at the position: as it is now, or as it was when removed.
  function showFile(now, before, changes, gone) {
    file.hidden = !selected;
    if (!selected) return;
    const { machine: m, path } = selected;
    const change = (changes[m] || {})[path] || null;
    const ghost = (gone[m] || {})[path] || null;
    const e = (now[m] || {})[path] || (change && change.kind === 'removed' ? (before[m] || {})[path] : null)
      || (ghost ? ghost.entry : null);
    const users = agentUsers(tl, m);
    const people = Object.keys(users).length;
    const present = Boolean((now[m] || {})[path]);
    const close = h('button', { type: 'button', class: 'btn btn-small tmp-close', title: 'close',
      onclick: () => { selected = null; draw(true); } }, '×');
    const chips = e ? [agentName(e.owner || '', 'tmp-owner'),
      e.group ? h('span', { class: 'bd-count tmp-mode', title: 'group' }, e.group) : null,
      h('span', { class: 'bd-count' }, fmtBytes(e.size)),
      h('span', { class: 'bd-count tmp-mode', title: e.acl ? e.acl.join(' ') : null }, modeText(e)),
      people && present ? h('span', { class: 'tmp-readers-label' }, h('span', { class: 'small muted' }, 'read by '),
        readersChip(readersOf(now[m], path, users), people)) : null,
      e.sha256 ? h('span', { class: 'bd-count tmp-mode', title: e.sha256 }, e.sha256.slice(0, 12)) : null] : [];
    const state = change ? h('span', { class: ['bd-count', `tmp-c-${change.kind}`] }, change.kind)
      : !present && ghost ? h('span', { class: 'bd-count tmp-c-removed' }, 'removed') : null;
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
