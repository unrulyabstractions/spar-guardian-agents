/* Router, header and breadcrumbs of the results browser.
 * route() renders the view for the current hash and runs the cleanups of the view it replaces.
 * renderShell() draws the top bar once at boot.
 *
 *   import { renderShell, route } from './router.js';
 *   renderShell();
 *   route();
 */
import { h } from './dom.js';
import { P, R, api, apiCache, callCache } from './api.js';
import { errorBox, link, mono } from './components.js';
import { viewRuns } from './view_runs.js';
import { viewRun } from './view_run.js';
import { viewEpisode } from './view_transcript.js';
import { viewContext } from './view_context.js';
import { viewSample } from './view_sample.js';
import { tip } from './charts.js';
import { viewTimeline } from './view_timeline.js';
import { viewSummary, viewSwarm } from './view_summary.js';

// Cleanups of the route on screen. Each route gets its own list, so a view that finishes
// loading after the user has left cannot leave a listener behind on the next view.
let cleanups = [];
let routeToken = 0;

function runCleanups(list) {
  for (const fn of list.splice(0)) { try { fn(); } catch (e) { /* ignore */ } }
}

function parseHash() {
  const raw = location.hash.replace(/^#/, '') || '/';
  const qi = raw.indexOf('?');
  const pathPart = qi >= 0 ? raw.slice(0, qi) : raw;
  const q = new URLSearchParams(qi >= 0 ? raw.slice(qi + 1) : '');
  let parts;
  try {
    parts = pathPart.split('/').filter(Boolean).map(decodeURIComponent);
  } catch (e) {
    return { view: 'notfound', raw };
  }
  if (!parts.length) return { view: 'summary', query: q };
  if (parts[0] === 'runs' && parts.length === 1) return { view: 'runs', query: q };
  if (parts[0] === 'swarm' && parts.length === 1) return { view: 'swarm', query: q };
  if (parts[0] === 'run' && parts.length >= 2) {
    const run = parts[1];
    if (parts.length === 2) return { view: 'run', run, query: q };
    if (parts[2] === 'episode' && parts.length >= 4) {
      return { view: 'episode', run, episode: parts.slice(3).join('/'), query: q };
    }
    if (parts[2] === 'context' && parts.length === 3) return { view: 'context', run, query: q };
    if (parts[2] === 'sample' && parts.length === 3) return { view: 'sample', run, query: q };
    if (parts[2] === 'timeline' && parts.length === 3) return { view: 'timeline', run, query: q };
  }
  return { view: 'notfound', raw };
}

export function renderShell() {
  const app = document.getElementById('app');
  app.replaceChildren(
    h('button', { type: 'button', class: 'skip-link', onclick: () => { const m = document.getElementById('main'); if (m) m.focus(); } }, 'Skip to content'),
    h('header', { class: 'topbar' },
      h('a', { class: 'brand', href: '#/' }, 'CheatBench × ORBIT'),
      h('nav', { class: 'nav-tabs', 'aria-label': 'Sections' },
        h('a', { class: 'nav-tab', id: 'nav-summary', href: '#/' }, 'Summary'),
        h('a', { class: 'nav-tab', id: 'nav-runs', href: '#/runs' }, 'Runs'),
        h('a', { class: 'nav-tab', id: 'nav-swarm', href: '#/swarm' }, 'Swarm')),
      h('nav', { class: 'crumbs', id: 'crumbs', 'aria-label': 'Breadcrumbs' }),
      h('button', {
        type: 'button', class: 'btn btn-small', title: 'Clear cached API responses and reload this view',
        onclick: () => { apiCache.clear(); callCache.clear(); route(); },
      }, 'Reload')),
    h('main', { id: 'main', tabindex: '-1' }));
}

function setCrumbs(r) {
  const nav = document.getElementById('crumbs');
  const items = [];
  const sep = () => h('span', { class: 'sep' }, '/');
  const sectionOf = { summary: 'summary', runs: 'runs', swarm: 'swarm' }[r.view] || (r.run ? 'runs' : null);
  for (const [id, key] of [['nav-summary', 'summary'], ['nav-runs', 'runs'], ['nav-swarm', 'swarm']]) {
    const el = document.getElementById(id);
    if (!el) continue;
    el.classList.toggle('active', sectionOf === key);
    if (sectionOf === key) el.setAttribute('aria-current', 'page'); else el.removeAttribute('aria-current');
  }
  if (r.run) {
    items.push(link(R.runs(), 'Runs', 'crumb'), sep());
    items.push(r.view === 'run' ? h('span', { class: 'crumb current' }, r.run) : link(R.run(r.run), r.run, 'crumb'));
    if (r.view === 'episode') items.push(sep(), h('span', { class: 'crumb current' }, `transcript ${r.episode}`));
    if (r.view === 'context') items.push(sep(), h('span', { class: 'crumb current' }, 'context windows'));
    if (r.view === 'sample') items.push(sep(), h('span', { class: 'crumb current' }, 'sample'));
    if (r.view === 'timeline') items.push(sep(), h('span', { class: 'crumb current' }, 'timeline'));
    const tab = (href, label, view, needsLog) => h('a', {
      href, class: ['tab', r.view === view && 'active'], dataset: needsLog ? { needsLog: '1' } : null,
    }, label);
    items.push(h('span', { class: 'tabs' },
      tab(R.run(r.run), 'run', 'run', false),
      tab(R.timeline(r.run), 'timeline', 'timeline', false),
      tab(R.context(r.run), 'context', 'context', true),
      tab(R.sample(r.run), 'sample', 'sample', true)));
  }
  nav.replaceChildren(...items);
}

/** Context windows and the sample come from the eval log; grey those tabs out when it is missing. */
function markEvalLogTabs(r, token) {
  if (!r.run) return;
  api(P.run(r.run)).then(run => {
    if (token !== routeToken || run.eval_log_exists) return;
    for (const a of document.querySelectorAll('#crumbs .tab[data-needs-log]')) {
      if (a.classList.contains('active')) continue;
      a.replaceWith(h('span', { class: 'tab disabled', title: 'eval log missing: no context windows or sample for this run' }, a.textContent));
    }
  }).catch(() => {});
}

const VIEWS = {
  summary: viewSummary,
  runs: viewRuns,
  swarm: viewSwarm,
  run: viewRun,
  episode: viewEpisode,
  context: viewContext,
  sample: viewSample,
  timeline: viewTimeline,
  notfound: async (r, view) => {
    view.mounted(() => { document.title = 'Unknown page · results'; });
    return h('div', { class: 'page' },
      h('h1', null, 'Unknown page'),
      h('p', null, 'No view matches ', mono('#' + (r.raw || '')), '. '),
      link('#/', 'Back to the summary'));
  },
};

let keepScrollOnce = false;   // set by a theme change: the view is drawn again without jumping to the top

/**
 * Render the view for the current hash. A view is `async (route, view) => Node`.
 * view.mounted(fn) runs fn once the node is on screen, and only if the user is still on
 * this route. view.cleanup(fn) runs fn when the user leaves. Views keep every global side
 * effect (document.title, history.replaceState, document listeners) behind these two.
 */
export async function route() {
  runCleanups(cleanups);
  tip.hide();
  const mine = [];
  cleanups = mine;
  const r = parseHash();
  const token = ++routeToken;
  setCrumbs(r);
  const main = document.getElementById('main');
  main.replaceChildren(h('div', { class: 'loading' }, 'Loading…'));
  const mounted = [];
  const view = { mounted: fn => mounted.push(fn), cleanup: fn => mine.push(fn) };
  try {
    const node = await VIEWS[r.view](r, view);
    if (token !== routeToken) { runCleanups(mine); return; }
    const y = keepScrollOnce ? window.scrollY : 0;
    keepScrollOnce = false;
    main.replaceChildren(node);
    window.scrollTo(0, y);
    for (const fn of mounted) fn();
    markEvalLogTabs(r, token);   // after the view, so a run view's own fetch of the run is in the cache
  } catch (e) {
    if (token !== routeToken) { runCleanups(mine); return; }
    document.title = 'Error · results';
    main.replaceChildren(h('div', { class: 'page' }, errorBox(e)));
  }
}

// Canvases and sequential fills read their colours from the tokens when drawn, so a change of the OS colour
// scheme draws the view again in place.
export function redrawForTheme() { keepScrollOnce = true; route(); }
