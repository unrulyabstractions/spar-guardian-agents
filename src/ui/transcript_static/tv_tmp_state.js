/* The temporary directories of a run's machines on the Board tab's clock, from tmp_snapshots.json (/api/tmp?id=,
 * src/replay/tmp_snapshots.py). Pure: no DOM, no import, so node runs it in tests/unit/test_transcript_viewer_tmp.py.
 *
 *   import { tmpTimeline, countAt, stateAt, changesIn, treeRows } from './tv_tmp_state.js';
 *   const tl = tmpTimeline(doc, (agent, k) => t);   // t: the call's place on the board's clock
 *   const c0 = countAt(tl, tPrev), c1 = countAt(tl, t);
 *   const now = stateAt(tl, c1);                     // {machine: {path: entry}}
 *   const ch = changesIn(tl, c0, c1);                // {machine: {path: {kind, by}}}
 *   treeRows(tl.dirs, now.default, ch.default, stateAt(tl, c0).default);
 *
 * A step is a call that ran in a machine ("ran": true), in the file's order (n). Its time is its call's t on the
 * board's clock, never earlier than the step before it, so the steps applied at any time are a prefix and every delta
 * applies to the state it was taken against. A machine's state before its first call is that call's `before` (its
 * baseline): no call ran in it earlier. A later call's `before` holds what changed between calls (by no call: `by`
 * null). A directory whose mtime or size alone changed is not changed: its entries say what changed. */

const DIR_NOISE = ['mtime', 'size'];

// Whether two entries of one path are the same, a directory's mtime and size left out.
export function sameEntry(a, b) {
  if (!a || !b) return a === b;
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  const dir = a.type === 'dir' && b.type === 'dir';
  for (const k of keys) {
    if (dir && DIR_NOISE.includes(k)) continue;
    if (a[k] !== b[k]) return false;
  }
  return true;
}

function apply(state, change) {
  const out = { ...state };
  for (const [path, entry] of Object.entries(change || {})) {
    if (entry === null) delete out[path];
    else out[path] = entry;
  }
  return out;
}

// The steps that ran, each with its machine's baseline flag and its time; the machines and their baselines.
export function tmpTimeline(doc, timeOf) {
  const seen = new Set();
  const steps = [];
  let last = -Infinity;
  for (const s of doc.steps || []) {
    if (!s.ran) continue;
    const t = timeOf(s.agent, s.k);
    last = Math.max(last, Number.isFinite(t) ? t : last === -Infinity ? 0 : last);
    steps.push({ n: s.n, agent: s.agent, k: s.k, machine: s.machine, t: last, first: !seen.has(s.machine),
      before: s.before || {}, after: s.after || {} });
    seen.add(s.machine);
  }
  const baseline = {};
  for (const s of steps) if (s.first) baseline[s.machine] = apply({}, s.before);
  const machines = Object.keys(doc.machines || {}).filter(m => m in baseline);
  for (const m of Object.keys(baseline)) if (!machines.includes(m)) machines.push(m);
  return { dirs: doc.dirs || [], machines, agents: doc.machines || {}, steps, times: steps.map(s => s.t), baseline,
    memo: new Map([[0, baseline]]) };
}

// How many steps have run at time t: those whose time is at or before it.
export function countAt(tl, t) {
  let lo = 0;
  let hi = tl.times.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (tl.times[mid] <= t) lo = mid + 1; else hi = mid;
  }
  return lo;
}

// Every machine's state once the first `count` steps have run (each machine's baseline before its first).
export function stateAt(tl, count) {
  if (tl.memo.has(count)) return tl.memo.get(count);
  let from = count;
  while (!tl.memo.has(from)) from -= 1;
  let state = tl.memo.get(from);
  for (let i = from; i < count; i += 1) {
    const s = tl.steps[i];
    const base = s.first ? state[s.machine] : apply(state[s.machine], s.before);
    state = { ...state, [s.machine]: apply(base, s.after) };
    tl.memo.set(i + 1, state);
  }
  return state;
}

// What steps c0 to c1 changed, per machine: {path: {kind: added | changed | removed, by}}, by the last step that
// touched the path ({n, agent, k}), or null for a change between calls.
export function changesIn(tl, c0, c1) {
  const s0 = stateAt(tl, c0);
  const s1 = stateAt(tl, c1);
  const by = {};
  for (let i = c0; i < c1; i += 1) {
    const s = tl.steps[i];
    const mine = by[s.machine] || (by[s.machine] = {});
    if (!s.first) for (const p of Object.keys(s.before)) mine[p] = null;
    for (const p of Object.keys(s.after)) mine[p] = { n: s.n, agent: s.agent, k: s.k };
  }
  const out = {};
  for (const m of tl.machines) {
    const a = s0[m] || {};
    const b = s1[m] || {};
    const got = {};
    for (const p of new Set([...Object.keys(a), ...Object.keys(b)])) {
      const kind = !(p in a) ? 'added' : !(p in b) ? 'removed' : sameEntry(a[p], b[p]) ? null : 'changed';
      if (kind) got[p] = { kind, by: (by[m] || {})[p] ?? null };
    }
    out[m] = got;
  }
  return out;
}

const parts = p => p.split('/').filter(Boolean);
function comparePaths(x, y) {
  const a = parts(x);
  const b = parts(y);
  for (let i = 0; i < Math.min(a.length, b.length); i += 1) {
    if (a[i] !== b[i]) return a[i] < b[i] ? -1 : 1;
  }
  return a.length - b.length;
}

// The rows of one machine's tree: the entries now and the paths removed in the window (from the state before it), the
// roots in the file's order, each path after its parent. Row: {path, name, depth, root, entry, change, parent}.
export function treeRows(dirs, now, changes, before) {
  const all = { ...(now || {}) };
  for (const [p, c] of Object.entries(changes || {})) if (c.kind === 'removed') all[p] = (before || {})[p];
  const rootOf = p => dirs.find(r => p === r || p.startsWith(r + '/'));
  const rows = [];
  for (const root of dirs) {
    const mine = Object.keys(all).filter(p => rootOf(p) === root).sort(comparePaths);
    for (const p of mine) {
      const depth = parts(p).length - parts(root).length;
      const cut = p.lastIndexOf('/');
      rows.push({ path: p, name: depth === 0 ? p : p.slice(cut + 1), depth, root, entry: all[p],
        change: (changes || {})[p] || null, parent: depth === 0 ? null : p.slice(0, cut) });
    }
  }
  return rows;
}
