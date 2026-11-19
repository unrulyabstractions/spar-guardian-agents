/* Plain-language names and meanings for every value the transcript viewer shows. Ported from collusion-hermaneutics'
 * viewer (viewer/static/app/labels.js). The collections come from /api/index (src/ui/transcript_viewer.py
 * COLLECTION_ABOUT); each run's seed post and arm come from the server; the ladders' levels and meanings come from
 * /api/ladders (src/ui/transcript_levels.py LADDERS). */

export const COLLECTIONS = {};
export const COLLECTION_ORDER = [];

/** Fill COLLECTIONS from the index's list, in its order. */
export function setCollections(list) {
  for (const c of list || []) {
    if (!(c.key in COLLECTIONS)) COLLECTION_ORDER.push(c.key);
    COLLECTIONS[c.key] = c;
  }
}
export const collectionOf = key => COLLECTIONS[key] || { key, label: key, tag: '', about: '' };

// A run's task by the stem of its cell's script (examples/<domain>/<stem>_<cell>.py).
export const TASKS = {
  binder: 'Protein design (binder)',
  factor: 'Prime factorization (factor)',
  flipt: 'Software engineering, Flipt issue (flipt)',
  vuls: 'Software engineering, Vuls issue (vuls)',
  game: 'Chess (game)',
  poem: 'Creative writing (poem)',
  riemann: 'Open math problem (riemann)',
  subset: 'Subset sum (subset)',
  worktime: 'Knowledge work (worktime)',
};

export const MODELS = {
  ds: 'DeepSeek V4.1 Flash', dsflash: 'DeepSeek V4.1 Flash', gpt5m: 'GPT-5 mini', haiku: 'Claude Haiku 5.5',
  luna: 'GPT-6 Luna', qwen: 'Qwen3.8 27B',
};

// Why a run is or is not counted (src/ui/transcript_viewer.py Listed.status).
export const STATUS = {
  valid: 'valid: counted',
  invalid: 'invalid: set aside by the study (its reason is on the run page)',
  incomplete: 'incomplete: the run did not finish',
  mock: 'mock: a mock-model rehearsal, no model spend',
  attempt: 'attempt: an earlier attempt a later run replaced',
  'set aside': 'set aside by the study',
  'extra copy': 'extra copy: a further valid copy of a run whose canonical copy is listed',
  'not indexed': 'not indexed: the study\'s INDEX.jsonl does not name the run',
};
export const STATUS_ORDER = Object.keys(STATUS);

// CheatBench's judge: the eval's own verdicts and the judge passes' (judges/cheatbench.<judge>.jsonl), both read
// through src/judging/verdict_files.py.
export const CHEATBENCH = {
  column: 'CheatBench judge',
  about: 'CheatBench\'s judge, written for a lone agent, read each agent\'s trajectory: whether it cheated and whether it saw the honeypots, with the task\'s own fields. It changes no rung. The team\'s word is how many workers it says cheated, of the workers it gave a parsed verdict.',
};

export const ROLE_LABEL = { worker: 'worker', regulator: 'hidden regulator', disregulator: 'hidden disregulator' };

export const armLabel = r => r.arm_label || r.arm || '';
export const taskLabel = r => TASKS[r.task] || (r.task_name ? r.task_name.replace(/_/g, ' ') : r.task) || '?';
export const modelLabel = r => MODELS[r.author] || r.author || r.model_id;
