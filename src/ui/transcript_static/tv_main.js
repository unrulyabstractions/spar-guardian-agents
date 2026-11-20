/* Boot and hash router of the transcript viewer (src/ui/transcript_viewer.py). Ported from collusion-hermaneutics'
 * viewer (viewer/static/app/main.js); our edits: the names, and the index loads before the first page so every page
 * knows the collections. The security rule is the results browser's (src/ui/static/app/main.js): every piece of run
 * data goes in through textContent or text nodes, never innerHTML.
 *
 *   #/                                every run, with filters (tv_view_index.js)
 *   #/run/<collection>/<run key>      one run: three ladders, evidence, judges, board, transcripts (tv_view_run.js)
 *       ?agent=<id>&call=<k>          opens that agent's transcript at tool call k
 *       ?tab=board&a=&b=&mode=&pos=   the Board tab: agents a and b, time | step, the position (tv_view_board.js)
 *       ?tab=details                  the Details tab: the three ladders' panels, the judges, the colour legend
 *   #/ladders?task=<task name>        the ladders and the level by task tables (tv_view_ladders.js)
 *       ?section=<id>                 opens the page at that section (reliability: how reliable each rung is)
 *   #/summary?col=<collection>        a collection's runs by workers' model, task and condition (tv_view_summary.js)
 */
import { h } from '/static/app/dom.js';
import { loadIndex, viewIndex } from './tv_view_index.js';
import { viewRun } from './tv_view_run.js';
import { viewLadders } from './tv_view_ladders.js';
import { viewSummary } from './tv_view_summary.js';

const main = () => document.getElementById('main');
let token = 0;

function shell() {
  const app = document.getElementById('app');
  app.replaceChildren(
    h('header', { class: 'topbar' },
      h('a', { class: 'brand', href: '#/' }, 'Transcripts'),
      h('span', { class: 'spacer' }),
      h('a', { class: 'btn btn-small', href: '#/ladders' }, 'Ladders'), ' ',
      h('a', { class: 'btn btn-small', href: '#/summary' }, 'Summary'), ' ',
      h('a', { class: 'btn btn-small', href: '#/' }, 'All runs')),
    h('main', { id: 'main', tabindex: '-1' }));
}

async function route() {
  const my = ++token;
  const hash = location.hash || '#/';
  const [path, qs] = hash.slice(1).split('?');
  const q = new URLSearchParams(qs || '');
  const after = [];
  const view = { mounted: fn => after.push(fn) };
  main().replaceChildren(h('div', { class: 'loading' }, 'Loading…'));
  try {
    await loadIndex();
    let node;
    const m = /^\/run\/(.+)$/.exec(path);
    if (m) node = await viewRun(m[1].split('/').map(decodeURIComponent).join('/'), q, view);
    else if (path === '/ladders') { node = await viewLadders(q, view); document.title = 'Ladders · Transcripts'; }
    else if (path === '/summary') { node = await viewSummary(q); document.title = 'Summary · Transcripts'; }
    else { node = await viewIndex(q); document.title = 'Runs · Transcripts'; }
    if (my !== token) return;
    main().replaceChildren(node);
    if (m) window.scrollTo(0, 0);
    for (const fn of after) fn();
  } catch (e) {
    if (my !== token) return;
    main().replaceChildren(h('div', { class: 'error-box' }, h('div', { class: 'error-title' }, 'Error'),
      h('pre', { class: 'pre' }, String((e && e.stack) || e))));
  }
}

shell();
// The index rewrites its own query with history.replaceState, which fires no hashchange, so every hashchange is a
// navigation (a link, the address bar, back and forward) and reroutes, a new query on the index included.
window.addEventListener('hashchange', route);
route();
