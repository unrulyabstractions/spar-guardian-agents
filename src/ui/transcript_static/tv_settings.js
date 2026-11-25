/* The viewer's settings, kept in this browser (localStorage): which tasks' runs the pages show. With nothing saved, the
 * tasks the study does not read (the index's left_out.tasks, src/measures/ladder_by_domain.py HIDDEN_TASKS) are off.
 * A change reroutes the page shown, so the runs page and the Summary page draw again with it. */
import { fmtInt, h } from '/static/app/dom.js';

const KEY = 'tv.hiddenTasks';
let DEFAULT = [];

/** The default from the index: the tasks it serves behind the setting. */
export function setDefaults(idx) { DEFAULT = (idx.left_out && idx.left_out.tasks) || []; }

/** The task names the pages leave out: the saved choice, else the default. */
export function hiddenTasks() {
  try {
    const v = JSON.parse(localStorage.getItem(KEY));
    if (Array.isArray(v)) return new Set(v);
  } catch (e) { /* a bad entry falls back to the default */ }
  return new Set(DEFAULT);
}

/** Whether the pages show a run of task `name` (a row's task_name). */
export const taskShown = name => !hiddenTasks().has(name);

const reroute = () => window.dispatchEvent(new HashChangeEvent('hashchange'));

/** The header's Settings control: a checkbox per task the index holds, with its runs, and Default. */
export function settingsMenu(idx) {
  const counts = new Map();
  for (const r of idx.rows) counts.set(r.task_name, (counts.get(r.task_name) || 0) + 1);
  const names = [...counts.keys()].sort((a, b) => String(a).localeCompare(String(b)));
  const box = h('div', { class: 'tv-settings-box' });
  const draw = () => {
    const off = hiddenTasks();
    box.replaceChildren(
      h('div', { class: 'tv-settings-head' }, 'Tasks'),
      ...names.map(n => h('label', { class: 'tv-settings-row' },
        h('input', { type: 'checkbox', checked: !off.has(n), onchange: e => {
          const now = hiddenTasks();
          if (e.target.checked) now.delete(n); else now.add(n);
          localStorage.setItem(KEY, JSON.stringify([...now].sort()));
          reroute();
        } }),
        h('span', null, String(n).replace(/_/g, ' ')), h('span', { class: 'muted small' }, fmtInt(counts.get(n))))),
      h('button', { type: 'button', class: 'btn btn-small', title: 'Hide the tasks the study does not read', onclick: () => {
        localStorage.removeItem(KEY);
        draw();
        reroute();
      } }, 'Default'));
  };
  draw();
  return h('details', { class: 'idx-about tv-settings' }, h('summary', { class: 'btn btn-small' }, 'Settings'), box);
}
