/* Facts about runs that several views read, with no DOM.
 * A run is a row of /api/runs (or the run page's /api/runs/<run>, which adds to it).
 *
 *   import { endingsText, isSwarmRun } from './run_facts.js';
 *   endingsText({ submit: 2, timeout: 1 });   // '2 submit, 1 timeout'
 */

/** A board run with hidden roles: its run.json records a composition. */
export const isSwarmRun = run => !!(run.swarm && run.swarm.composition != null);

/** Agents in a hidden-roles run: regulators, disregulators and workers, else the recorded size. */
export const swarmSize = sw => ((sw.regulators || 0) + (sw.disregulators || 0) + (sw.workers || 0)) || sw.n_agents || 0;

/** The episode of one agent, or null. */
export const episodeOf = (run, agent) => ((run && run.episodes) || []).find(e => e.orbit_agent === agent) || null;

/** How a run's episodes ended, most common first: "2 submit, 1 timeout". */
export function endingsText(endings) {
  return Object.entries(endings || {}).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([k, n]) => `${n} ${k}`).join(', ');
}

/** Sort key of a setting: its task order, team size and composition, then the run name. */
export function settingSortKey(s, run) {
  const pad = x => String(x == null ? 0 : x).padStart(3, '0');
  return `${s.order}|${pad(s.n)}|${pad(s.R)}|${pad(s.D)}|${run}`;
}
