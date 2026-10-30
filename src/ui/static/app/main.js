/* Results browser for ORBIT x CheatBench runs: the boot module that index.html loads.
 *
 * Vanilla ES2020 modules, no framework, no build step, no network access beyond the local API.
 * Every piece of run data (transcripts, tool output, board posts, reasons, errors) is
 * inserted with textContent or text nodes. Nothing from the API ever reaches innerHTML.
 *
 *   <script type="module" src="/static/app/main.js"></script>
 *
 * Modules
 *   dom.js               DOM helpers and formatting
 *   api.js               API client
 *   run_facts.js         Facts about runs that several views read (no DOM)
 *   charts.js            Chart helpers (SVG, tooltip, colour ramp, ticks, legend glyphs)
 *   components.js        Shared components (badges, tables, controls, tool calls, verdicts, ladders)
 *   router.js            Router, header and breadcrumbs
 *   view_runs.js         View: runs                #/runs  (one row per run, opening to its agents)
 *   view_run.js          View: run page            #/run/<run>
 *   view_transcript.js   View: transcript          #/run/<run>/episode/<episode>
 *   view_context.js      View: context windows     #/run/<run>/context?agent=<name>&call=<i>
 *   view_sample.js       View: sample              #/run/<run>/sample
 *   view_timeline.js     View: timeline            #/run/<run>/timeline (raster, swimlanes, exposure cascade)
 *   view_summary.js      Views: summary            #/        (cheating by setting, composition grids, swarm runs)
 *                        and swarm visualizations  #/swarm   (grids, funnel, adoption curves, run explorer)
 *   main.js              Boot
 */
import { redrawForTheme, renderShell, route } from './router.js';

renderShell();
window.addEventListener('hashchange', route);
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', redrawForTheme);
route();
