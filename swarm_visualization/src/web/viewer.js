// The swarm in 3D. Each node is an agent; edges grow as agents talk on the board (a post naming the other, or a
// read asking for the other's posts), thicker the more they go back and forth. Coloured orbs are the exploit's
// pieces: each agent circles the pieces it holds, and an orb flies to an agent when it gets a piece it lacked.
// Nodes warm and then turn red as they climb the Cheating ladder. No text is drawn anywhere.
import * as THREE from 'three';

const S = JSON.parse(document.getElementById('story').textContent);
const N = S.agents.length;
const T = Math.max(S.duration, 1e-6);
const PLAY_SECONDS = 120;                // the whole run plays in this many seconds at 1x
const SPEEDS = [0.25, 0.5, 1, 2];
const SPS = T / PLAY_SECONDS;            // story seconds per screen second at 1x
const D = {spark: 1.3 * SPS, flow: 2.4 * SPS, ripple: 1.5 * SPS, flash: 1.8 * SPS, submit: 3.6 * SPS,
           fade: 0.7 * SPS, alert: 3.2 * SPS};
const ASK = 1;
const CHEATED = 3;

const RUNG_COLORS = ['#c9d6e8', '#ffe1a6', '#ff9d47', '#ff2a2a', '#ff0a3c'].map(c => new THREE.Color(c));
const PIECE_COLORS = ['#3d8bff', '#2fe07a', '#b46dff', '#00d8d0', '#f0e040', '#ff7ad9'].map(c => new THREE.Color(c));
const EDGE_COLOR = new THREE.Color('#8fb3ff');
const EDGE_RED = new THREE.Color('#ff2f45');
const TALK_COLOR = new THREE.Color('#eaf2ff');
const ASK_COLOR = new THREE.Color('#7fe8ff');
const ALERT_COLOR = new THREE.Color('#ffc21a');          // an alert to humans; a false alarm is grey
const FALSE_ALARM = new THREE.Color('#a3abc2');
const ALERTS = (S.alerts || []).slice().sort((a, b) => a[0] - b[0]);
// A hand-in: only the final one is graded (the labels), so an earlier one is neutral steel blue and the final one
// green when the team was honest, red when it cheated.
const HANDIN_COLOR = new THREE.Color('#9db4e0');
const HONEST_COLOR = new THREE.Color('#3ddc84');
const CHEAT_COLOR = new THREE.Color('#ff2f45');
const TAKEBACK_COLOR = new THREE.Color('#d68cff');        // a hand-in taken back
const HANDINS = (S.handins || []).slice().sort((a, b) => a[0] - b[0]);
if (!HANDINS.length && S.submit) HANDINS.push([S.submit.t, S.submit.agent, 0, '']);
// The final hand-in: the last one no take-back followed.
const FINAL = (() => {
  const last = HANDINS[HANDINS.length - 1];
  return S.submit && last && last[2] === 0 ? last : null;
})();
const FINAL_T = FINAL ? FINAL[0] : (S.submit ? S.submit.t : Infinity);
const VERDICT_COLOR = S.submit && S.submit.cheated ? CHEAT_COLOR : HONEST_COLOR;
const TAU = 0.08 * T;                                     // an exchange's weight halves in about 0.7 TAU
function handinAt(t) {                                    // who holds the hand-in at t, since when; last take-back
  let holder = -1, since = -Infinity, back = null;
  for (const row of HANDINS) {
    if (row[0] > t) break;
    if (row[2] === 0) { holder = row[1]; since = row[0]; } else { holder = -1; since = row[0]; back = row; }
  }
  return {holder, since, back};
}

// ---------- scene ----------
const canvas = document.getElementById('scene');
const renderer = new THREE.WebGLRenderer({canvas, antialias: true, alpha: true});
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.setClearColor(0x000000, 0);
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 4000);
scene.add(new THREE.HemisphereLight(0xc4d6ff, 0x141824, 1.1));
const keyLight = new THREE.DirectionalLight(0xffffff, 1.6);
keyLight.position.set(12, 22, 16);
scene.add(keyLight);
const rimLight = new THREE.DirectionalLight(0x6f8cff, 0.8);
rimLight.position.set(-18, -6, -12);
scene.add(rimLight);

function radialTexture(stops) {
  const c = document.createElement('canvas');
  c.width = c.height = 256;
  const g = c.getContext('2d');
  const gradient = g.createRadialGradient(128, 128, 0, 128, 128, 128);
  for (const [at, alpha] of stops) gradient.addColorStop(at, `rgba(255,255,255,${alpha})`);
  g.fillStyle = gradient;
  g.fillRect(0, 0, 256, 256);
  const texture = new THREE.CanvasTexture(c);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}
const GLOW = radialTexture([[0, 1], [0.2, 0.6], [0.5, 0.16], [1, 0]]);
const RING = radialTexture([[0, 0], [0.7, 0], [0.8, 0.95], [0.88, 0.3], [1, 0]]);

function sprite(texture, color = 0xffffff, opacity = 1) {
  const material = new THREE.SpriteMaterial({map: texture, color, transparent: true, opacity,
                                             blending: THREE.AdditiveBlending, depthWrite: false});
  return new THREE.Sprite(material);
}

// A faint field of stars for depth.
{
  const count = 1400, positions = new Float32Array(count * 3);
  for (let i = 0; i < count; i++) {
    const u = Math.random() * 2 - 1, a = Math.random() * Math.PI * 2, r = 300 + Math.random() * 500;
    const s = Math.sqrt(1 - u * u);
    positions.set([r * s * Math.cos(a), r * u, r * s * Math.sin(a)], i * 3);
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  scene.add(new THREE.Points(geometry, new THREE.PointsMaterial({color: 0x8090b0, size: 1.4, sizeAttenuation: true,
                                                                transparent: true, opacity: 0.55})));
}

// Pools of throwaway objects: begin() each frame, get() what is needed, end() hides the rest.
class Pool {
  constructor(make) { this.make = make; this.items = []; this.used = 0; }
  begin() { this.used = 0; }
  get() {
    if (this.used >= this.items.length) { const o = this.make(); scene.add(o); this.items.push(o); }
    const o = this.items[this.used++];
    o.visible = true;
    return o;
  }
  end() { for (let k = this.used; k < this.items.length; k++) this.items[k].visible = false; }
}
const glowPool = new Pool(() => sprite(GLOW));
const ringPool = new Pool(() => sprite(RING));
const orbGeometry = new THREE.SphereGeometry(1, 20, 14);
const orbPool = new Pool(() => new THREE.Mesh(orbGeometry, new THREE.MeshStandardMaterial({
  roughness: 0.25, metalness: 0.1, emissiveIntensity: 1.2, transparent: true})));
const pools = [glowPool, ringPool, orbPool];

// ---------- agents ----------
function spikyGeometry() {
  // A spiky ball: a once-subdivided icosahedron whose every face is pulled out into a point.
  const base = new THREE.IcosahedronGeometry(0.9, 1).toNonIndexed();
  const p = base.getAttribute('position');
  const out = [];
  const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3(), tip = new THREE.Vector3();
  for (let f = 0; f < p.count; f += 3) {
    a.fromBufferAttribute(p, f); b.fromBufferAttribute(p, f + 1); c.fromBufferAttribute(p, f + 2);
    tip.copy(a).add(b).add(c).normalize().multiplyScalar(1.5);
    for (const [u, v] of [[a, b], [b, c], [c, a]]) out.push(u.x, u.y, u.z, v.x, v.y, v.z, tip.x, tip.y, tip.z);
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(out, 3));
  geometry.computeVertexNormals();
  return geometry;
}

const pos = [], vel = [];
const nodes = S.agents.map((agent, i) => {
  const group = new THREE.Group();
  scene.add(group);
  const material = new THREE.MeshStandardMaterial({color: RUNG_COLORS[0].clone(), emissive: RUNG_COLORS[0].clone(),
                                                   emissiveIntensity: 0.25, roughness: 0.32, metalness: 0.12,
                                                   transparent: true, flatShading: agent.role === 'disregulator'});
  let core, cage = null;
  if (agent.role === 'disregulator') {
    core = new THREE.Mesh(spikyGeometry(), material);
  } else if (agent.role === 'regulator') {
    const box = new THREE.BoxGeometry(1.5, 1.5, 1.5);
    core = new THREE.Mesh(box, material);
    material.flatShading = true;
    cage = new THREE.LineSegments(new THREE.EdgesGeometry(box),
                                  new THREE.LineBasicMaterial({color: 0xcfe2ff, transparent: true, opacity: 0.9}));
    core.add(cage);
  } else {
    core = new THREE.Mesh(new THREE.SphereGeometry(1, 48, 32), material);
  }
  core.userData.agent = i;
  group.add(core);
  const glow = sprite(GLOW, RUNG_COLORS[0], 0.4);
  glow.scale.setScalar(4);
  group.add(glow);
  const complete = new THREE.Mesh(new THREE.TorusGeometry(1.95, 0.05, 8, 72),
                                  new THREE.MeshBasicMaterial({color: 0xffffff, transparent: true, opacity: 0,
                                                               blending: THREE.AdditiveBlending, depthWrite: false}));
  group.add(complete);
  // Start on a sphere (Fibonacci points) so no two agents begin in the same place.
  const k = i + 0.5, phi = Math.acos(1 - 2 * k / N), theta = Math.PI * (1 + Math.sqrt(5)) * k;
  const r = 5 + Math.sqrt(N) * 1.6;
  pos.push(new THREE.Vector3(r * Math.sin(phi) * Math.cos(theta), r * Math.cos(phi), r * Math.sin(phi) * Math.sin(theta)));
  vel.push(new THREE.Vector3());
  const tilt = new THREE.Quaternion().setFromEuler(new THREE.Euler((i * 1.7) % Math.PI, (i * 2.3) % Math.PI, 0));
  return {agent, group, core, material, cage, glow, complete, tilt, spin: 0.6 + (i % 5) * 0.12};
});
const cores = nodes.map(n => n.core);
for (const node of nodes) {
  node.flag = new THREE.Mesh(new THREE.TorusGeometry(1.7, 0.07, 8, 72),
                             new THREE.MeshBasicMaterial({color: ALERT_COLOR, transparent: true, opacity: 0,
                                                          blending: THREE.AdditiveBlending, depthWrite: false}));
  node.group.add(node.flag);
  node.hold = new THREE.Mesh(new THREE.TorusGeometry(2.25, 0.11, 8, 6),
                             new THREE.MeshBasicMaterial({color: HANDIN_COLOR, transparent: true, opacity: 0,
                                                          blending: THREE.AdditiveBlending, depthWrite: false}));
  node.group.add(node.hold);
}
function alertsUpTo(t) {                     // agent -> how many true or unknown alerts named it, and raised by it
  const named = new Array(N).fill(0), raised = new Array(N).fill(0);
  for (const [ta, caller, who, outcome] of ALERTS) {
    if (ta > t) break;
    raised[caller]++;
    if (outcome !== 0) for (const j of who) named[j]++;
  }
  return {named, raised};
}

// ---------- words: a tag under each agent, the cast, the clock, a feed of events, a legend ----------
const RUNG_WORDS = ['none', 'looked', 'tempted', 'attempted', 'hacked'];
const RUNG_EVENTS = ['', 'looks at the exploit', 'is tempted to use it', 'tries the exploit', 'hacks: the exploit works'];
const ROLE_WORDS = {worker: 'worker', regulator: 'regulator', disregulator: 'disregulator'};
const ROLE_ACCENT = {worker: '#8fb3ff', regulator: '#7fe8ff', disregulator: '#ff9de2'};
const esc = text => String(text).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
const hex = color => '#' + color.getHexString();
const nameOf = i => (i >= 0 ? `<b style="color:${ROLE_ACCENT[S.agents[i].role] || '#fff'}">${esc(S.agents[i].label)}</b>` : 'outside');
const clockText = v => { const s = Math.max(0, Math.round(v)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };

const labelsBox = document.getElementById('labels');
const tags = S.agents.map(agent => {
  const el = document.createElement('div');
  el.className = 'tag';
  // A big swarm gets short tags (label and rung); the header already names the models and roles.
  el.innerHTML = N > 8
    ? `<b style="color:${ROLE_ACCENT[agent.role] || '#fff'}">${esc(agent.label)}</b> <span class="rung"></span>`
    : `<b style="color:${ROLE_ACCENT[agent.role] || '#fff'}">${esc(agent.label)}</b> <span class="model">${esc(agent.model || '')} ${ROLE_WORDS[agent.role] || agent.role}</span><br><span class="rung"></span>`;
  labelsBox.appendChild(el);
  el.insertAdjacentHTML('beforeend', ' <span class="flagged"></span>');
  return {el, rung: el.querySelector('.rung'), flagged: el.querySelector('.flagged'), shown: -1, flags: ''};
});
{
  const groups = new Map();
  for (const a of S.agents) {
    const key = `${a.model} ${a.role}`;
    groups.set(key, (groups.get(key) || 0) + 1);
  }
  document.getElementById('cast').textContent = [...groups].map(([k, n]) => `${n} ${k}${n > 1 ? 's' : ''}`).join(' · ');
  document.getElementById('runName').textContent = S.run;
  const shapes = [['worker', '● worker'], ['regulator', '■ regulator'], ['disregulator', '✹ disregulator']]
    .filter(([role]) => S.agents.some(a => a.role === role)).map(([, word]) => word).join('&nbsp;&nbsp;');
  const swatches = RUNG_WORDS.map((w, k) => `<span class="sw${k ? '' : ' first'}" style="background:${hex(RUNG_COLORS[k])}"></span>${w}`).join('');
  const line = '<span class="line"></span>talk';
  const orb = S.pieces ? `<span class="sw" style="background:${hex(PIECE_COLORS[0])}"></span>piece` : '';
  const alarm = ALERTS.length ? `<span class="alarm">⚠</span>alert` : '';
  const hands = HANDINS.length
    ? `<br><span class="sw first" style="background:${hex(HANDIN_COLOR)}"></span>handed in` +
      (S.submit ? `<span class="sw" style="background:${hex(VERDICT_COLOR)}"></span>final: ${S.submit.cheated ? 'cheated' : 'honest'}` : '') +
      (HANDINS.some(r => r[2] === 1) ? `<span class="sw" style="background:${hex(TAKEBACK_COLOR)}"></span>taken back` : '') : '';
  document.getElementById('legend').innerHTML = `${shapes}<br>${swatches}<br>${line}${orb}${alarm}${hands}`;
}
const events = [];
for (const [te, a, text] of S.said || []) {
  events.push({t: te, accent: ROLE_ACCENT[S.agents[a].role], html: `${nameOf(a)} posts <span class="quote">“${esc(text)}”</span>`});
}
S.agents.forEach((agent, i) => {
  let before = 0;
  for (const [at, value] of agent.rungs) {
    if (value > before) {
      const atEnd = at >= T - 1e-6;
      events.push({t: at, accent: hex(RUNG_COLORS[value]),
                   html: `${nameOf(i)} ${RUNG_EVENTS[value]}${atEnd ? ' (final grade)' : ''}`});
    }
    before = value;
  }
});
for (const [tf, piece, src, dst] of S.flows) {
  const name = (S.piece_names || [])[piece] ?? piece;
  events.push({t: tf, accent: hex(PIECE_COLORS[piece % PIECE_COLORS.length]),
               html: `${nameOf(dst)} gets piece <b>${esc(name)}</b> of the exploit${src >= 0 ? ` from ${nameOf(src)}` : ''}`});
}
if (S.submit) {
  events.push({t: FINAL_T, accent: S.submit.cheated ? '#ff2d3d' : '#a8ffc4',
               html: `${nameOf(S.submit.agent)} hands in the team's fix: <b>${S.submit.passed ? 'passed' : 'failed'}</b>` +
                     `${S.submit.cheated ? ', <b style="color:#ff5a6a">by cheating</b>' : ', honestly'}`});
}
{
  const finalAt = FINAL ? FINAL[0] : undefined;
  for (const [th, a, kind, reason] of HANDINS) {
    if (kind === 0 && th === finalAt) continue;
    events.push(kind === 0
      ? {t: th, accent: hex(HANDIN_COLOR), html: `${nameOf(a)} hands in the team's fix (not graded)`}
      : {t: th, accent: hex(TAKEBACK_COLOR),
         html: `${nameOf(a)} takes the hand-in back${reason ? `<br><span class="quote">“${esc(reason)}”</span>` : ''}`});
  }
}
for (const [ta, caller, who, outcome, reason] of ALERTS) {
  const verdict = outcome === 1 ? 'true alarm' : outcome === 0 ? 'false alarm' : '';
  events.push({t: ta, accent: outcome === 0 ? hex(FALSE_ALARM) : hex(ALERT_COLOR),
               html: `<span class="alarm">⚠</span>${nameOf(caller)} alerts humans` +
                     `${who.length ? ` about ${who.map(nameOf).join(', ')}` : ''}` +
                     `${verdict ? ` <b style="color:${outcome === 0 ? hex(FALSE_ALARM) : hex(ALERT_COLOR)}">${verdict}</b>` : ''}` +
                     `<br><span class="quote">“${esc(reason)}”</span>`});
}
events.sort((a, b) => a.t - b.t);
const feed = document.getElementById('feed');
const clockTime = document.getElementById('clockTime');
const whereEl = document.getElementById('where');
const FEED_SIZE = 4;
let feedShown = -2;
function drawWords(rungs, here, alive, focus, flags, hand) {
  clockTime.textContent = `${clockText(t)} / ${clockText(T)}`;
  const r = roundAt(t), step = stepShown();
  const whereText = (ROUNDS.length ? `round ${ROUNDS[r] ? ROUNDS[r][0] : 0} / ${ROUNDS[ROUNDS.length - 1][0]}` : '') +
                    (step ? ` · step ${cursor + 1} / ${STEPS.length}: ${nameOf(step[1])} ${esc(step[2])}` : '');
  if (whereEl.innerHTML !== whereText) whereEl.innerHTML = whereText;
  let last = -1;
  while (last + 1 < events.length && events[last + 1].t <= t) last++;
  if (last !== feedShown) {
    feedShown = last;
    const rows = [];
    for (let k = last; k >= 0 && k > last - FEED_SIZE; k--) {
      const e = events[k], age = last - k;
      rows.push(`<div class="event" style="border-left-color:${e.accent};opacity:${1 - age * 0.13}">` +
                `<span class="when">${clockText(e.t)}</span>${e.html}</div>`);
    }
    feed.innerHTML = rows.join('');
  }
  camera.updateMatrixWorld();
  const w = window.innerWidth, h = window.innerHeight;
  nodes.forEach((node, i) => {
    const tag = tags[i];
    if (!alive[i] || here[i].here < 0.5) { tag.el.style.display = 'none'; return; }
    tmp.copy(pos[i]).project(camera);
    if (tmp.z > 1) { tag.el.style.display = 'none'; return; }
    const lift = 1.9 * node.group.scale.x;
    const below = new THREE.Vector3().copy(pos[i]).addScaledVector(camera.up, -lift).project(camera);
    tag.el.style.display = '';
    tag.el.style.left = `${(tmp.x * 0.5 + 0.5) * w}px`;
    tag.el.style.top = `${(-below.y * 0.5 + 0.5) * h + 4}px`;
    tag.el.style.opacity = String((1 - 0.6 * here[i].gone) * (focus(i) < 1 ? 0.25 : 1));
    const rung = rungs[i].rung;
    const badge = (flags.named[i] ? `⚠${flags.named[i] > 1 ? flags.named[i] : ''}` : '') +
                  (hand.holder === i ? ` <b style="color:${hand.since === FINAL_T ? hex(VERDICT_COLOR) : hex(HANDIN_COLOR)}">✓</b>` : '') +
                  (hand.back && hand.back[1] === i && t - hand.back[0] < D.flash * 1.5 ? ' <b style="color:#d68cff">↩</b>' : '');
    if (tag.flags !== badge) {
      tag.flags = badge;
      tag.flagged.innerHTML = badge;
    }
    if (tag.shown !== rung) {
      tag.shown = rung;
      tag.rung.textContent = RUNG_WORDS[rung];
      tag.rung.style.color = hex(RUNG_COLORS[rung]);
    }
  });
}

// ---------- edges ----------
const pairs = new Map();
for (const [t, from, to, kind] of S.talk) {
  const i = Math.min(from, to), j = Math.max(from, to), key = i * 1000 + j;
  if (!pairs.has(key)) pairs.set(key, {i, j, ij: [], ji: [], all: [], w: 0, last: -Infinity, mesh: null});
  const pair = pairs.get(key);
  (from === i ? pair.ij : pair.ji).push(t);
  pair.all.push(t);
}
const cylinder = new THREE.CylinderGeometry(1, 1, 1, 10, 1, true);
for (const pair of pairs.values()) {
  pair.ij.sort((a, b) => a - b); pair.ji.sort((a, b) => a - b); pair.all.sort((a, b) => a - b);
  pair.mesh = new THREE.Mesh(cylinder, new THREE.MeshBasicMaterial({color: EDGE_COLOR.clone(), transparent: true,
                                                                     opacity: 0, blending: THREE.AdditiveBlending,
                                                                     depthWrite: false}));
  scene.add(pair.mesh);
}
// The busiest pair's final weight: edge thickness is read against it.
function decayed(sorted, t) {                 // each exchange counts 1 when it happens and fades with time
  let sum = 0;
  for (const te of sorted) { if (te > t) break; sum += Math.exp(-(t - te) / TAU); }
  return sum;
}
function pairWeight(pair, t) {                // back and forth counts double
  const ij = decayed(pair.ij, t), ji = decayed(pair.ji, t);
  return ij + ji + 2 * Math.min(ij, ji);
}
// The busiest moment of the busiest pair: edge thickness is read against it.
const W_MAX = Math.max(1, ...[...pairs.values()].flatMap(p => p.all.map(te => pairWeight(p, te))));
const talkSorted = S.talk.slice().sort((a, b) => a[0] - b[0]);
const postsSorted = S.posts.slice().sort((a, b) => a[0] - b[0]);

function countUpTo(sorted, t) {              // how many values of a sorted list are <= t
  let lo = 0, hi = sorted.length;
  while (lo < hi) { const mid = (lo + hi) >> 1; if (sorted[mid] <= t) lo = mid + 1; else hi = mid; }
  return lo;
}
function firstFrom(sortedRows, t) {          // the first row whose time is > t
  let lo = 0, hi = sortedRows.length;
  while (lo < hi) { const mid = (lo + hi) >> 1; if (sortedRows[mid][0] <= t) lo = mid + 1; else hi = mid; }
  return lo;
}
function* windowed(sortedRows, t, span) {    // rows with t - span < time <= t
  const end = firstFrom(sortedRows, t);
  for (let k = firstFrom(sortedRows, t - span); k < end; k++) yield sortedRows[k];
}

// ---------- state at time t ----------
function rungAt(agent, t) {
  let rung = 0, previous = 0, since = -Infinity;
  for (const [at, value] of agent.rungs) {
    if (at > t) break;
    if (value !== rung) { previous = rung; rung = value; since = at; }
  }
  return {rung, previous, since};
}
function presence(agent, t) {               // 0 not yet here, 1 here, fading to the exited look after its end
  if (t < agent.start) return {here: 0, gone: 0};
  const here = Math.min(1, (t - agent.start) / D.fade + (agent.start <= 0 ? 1 : 0));
  const leaves = agent.end < T - 1e-6;
  const gone = leaves && t > agent.end ? Math.min(1, (t - agent.end) / D.fade) : 0;
  return {here, gone};
}
const flows = S.flows.slice().sort((a, b) => a[0] - b[0]);
function piecesHeld(i, t) {
  const held = new Set(S.agents[i].holds);
  for (const [tf, piece, , to] of flows) {
    if (tf + D.flow > t) break;
    if (to === i) held.add(piece);
  }
  return [...held].sort((a, b) => a - b);
}
function farPoint(seed) {                    // where a piece no agent held comes in from
  const a = seed * 2.399, u = Math.sin(seed * 12.9898) * 0.6;
  const s = Math.sqrt(1 - u * u);
  return new THREE.Vector3(60 * s * Math.cos(a), 60 * u, 60 * s * Math.sin(a));
}
const smooth = x => { x = Math.min(1, Math.max(0, x)); return x * x * (3 - 2 * x); };

// ---------- layout ----------
const force = Array.from({length: N}, () => new THREE.Vector3());
const tmp = new THREE.Vector3();
function stepLayout(alive) {
  for (const f of force) f.set(0, 0, 0);
  for (let i = 0; i < N; i++) {
    if (!alive[i]) continue;
    for (let j = i + 1; j < N; j++) {
      if (!alive[j]) continue;
      tmp.subVectors(pos[j], pos[i]);
      const d2 = Math.max(tmp.lengthSq(), 0.25);
      tmp.normalize().multiplyScalar(40 / d2);
      force[i].sub(tmp); force[j].add(tmp);
    }
  }
  for (const pair of pairs.values()) {
    if (pair.w <= 0 || !alive[pair.i] || !alive[pair.j]) continue;
    tmp.subVectors(pos[pair.j], pos[pair.i]);
    const d = Math.max(tmp.length(), 0.01);
    const rest = 3.4 + 8 / (1 + 0.5 * pair.w);
    const k = 0.02 * Math.min(1 + Math.sqrt(pair.w), 6);
    tmp.multiplyScalar(((d - rest) * k) / d);
    force[pair.i].add(tmp); force[pair.j].sub(tmp);
  }
  for (let i = 0; i < N; i++) {
    if (!alive[i]) continue;
    force[i].addScaledVector(pos[i], -0.018);
    vel[i].add(force[i].multiplyScalar(0.05)).multiplyScalar(0.86);
    if (vel[i].length() > 0.6) vel[i].setLength(0.6);
    pos[i].add(vel[i]);
  }
}

// ---------- camera ----------
let azimuth = 0.7, polar = 1.15, zoom = 1, lastDrag = -1e9, dragging = false, px = 0, py = 0;
const center = new THREE.Vector3();
let radius = 12;
canvas.addEventListener('pointerdown', e => { dragging = true; px = e.clientX; py = e.clientY; canvas.setPointerCapture(e.pointerId); });
canvas.addEventListener('pointerup', e => { dragging = false; canvas.releasePointerCapture(e.pointerId); });
canvas.addEventListener('pointermove', e => {
  pointer.x = (e.clientX / window.innerWidth) * 2 - 1;
  pointer.y = -(e.clientY / window.innerHeight) * 2 + 1;
  pointerSeen = true;
  if (!dragging) return;
  azimuth -= (e.clientX - px) * 0.006;
  polar = Math.min(2.9, Math.max(0.25, polar - (e.clientY - py) * 0.006));
  px = e.clientX; py = e.clientY;
  lastDrag = performance.now();
});
canvas.addEventListener('pointerleave', () => { pointerSeen = false; });
canvas.addEventListener('wheel', e => { e.preventDefault(); zoom = Math.min(3, Math.max(0.3, zoom * Math.exp(e.deltaY * 0.001))); },
                        {passive: false});
function placeCamera(alive, dt) {
  const c = new THREE.Vector3();
  let n = 0;
  for (let i = 0; i < N; i++) if (alive[i]) { c.add(pos[i]); n++; }
  if (n) c.divideScalar(n);
  let r = 4;
  for (let i = 0; i < N; i++) if (alive[i]) r = Math.max(r, pos[i].distanceTo(c) + 3);
  const ease = 1 - Math.exp(-dt * 2);
  center.lerp(c, ease);
  radius += (r - radius) * ease;
  const distance = (radius / Math.sin((camera.fov * Math.PI) / 360)) * 0.95 * zoom;
  camera.position.set(center.x + distance * Math.sin(polar) * Math.cos(azimuth),
                      center.y + distance * Math.cos(polar),
                      center.z + distance * Math.sin(polar) * Math.sin(azimuth));
  camera.lookAt(center);
}

// ---------- hover ----------
const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();
let pointerSeen = false, hovered = -1;
function pick() {
  if (!pointerSeen || dragging) return -1;
  raycaster.setFromCamera(pointer, camera);
  const hit = raycaster.intersectObjects(cores.filter(c => c.parent.visible), false)[0];
  return hit ? hit.object.userData.agent : -1;
}

// ---------- the frame ----------
let t = 0, playing = false, speedIndex = 2, clock = 0, last = performance.now();
const tint = document.getElementById('tint');
const handinRoom = document.getElementById('handinRoom');
const honestRoom = document.getElementById('honestRoom');
const arc = new THREE.Vector3(), up = new THREE.Vector3(), from = new THREE.Vector3(), to = new THREE.Vector3();

function update(dt) {
  const alive = S.agents.map(a => t >= a.start);
  const rungs = S.agents.map(a => rungAt(a, t));
  const here = S.agents.map(a => presence(a, t));

  for (const pair of pairs.values()) {
    pair.w = pairWeight(pair, t);
    const n = countUpTo(pair.all, t);
    pair.last = n ? pair.all[n - 1] : -Infinity;
  }
  for (let s = 0; s < 2; s++) stepLayout(alive);
  hovered = pick();
  const near = new Set();
  if (hovered >= 0) {
    near.add(hovered);
    for (const pair of pairs.values()) if (pair.w > 0 && (pair.i === hovered || pair.j === hovered)) { near.add(pair.i); near.add(pair.j); }
  }
  const focus = i => (hovered < 0 || near.has(i) ? 1 : 0.18);

  // Nodes: colour by rung, faded once the agent has left.
  nodes.forEach((node, i) => {
    const {rung, previous, since} = rungs[i];
    const {here: h, gone} = here[i];
    node.group.visible = alive[i];
    if (!alive[i]) return;
    node.group.position.copy(pos[i]);
    const mix = smooth((t - since) / D.fade);
    const color = RUNG_COLORS[previous].clone().lerp(RUNG_COLORS[rung], mix);
    node.material.color.copy(color);
    node.material.emissive.copy(color);
    const pulse = rung >= CHEATED ? 0.5 + 0.5 * Math.sin(clock * (rung === 4 ? 5 : 3.2)) : 0;
    node.material.emissiveIntensity = 0.18 + 0.2 * rung + 0.35 * pulse;
    const look = 1 - 0.7 * gone;
    node.material.opacity = (0.35 + 0.65 * look) * focus(i);
    const scale = smooth(h) * (1 - 0.3 * gone) * (rung >= CHEATED ? 1.08 : 1);
    node.group.scale.setScalar(Math.max(0.001, scale));
    node.glow.material.color.copy(color);
    node.glow.material.opacity = (0.28 + 0.1 * rung + 0.35 * pulse) * look * focus(i);
    node.glow.scale.setScalar(3.4 + 0.75 * rung + 0.8 * pulse);
    node.core.rotation.y += dt * 0.3;
    if (node.cage) { node.core.rotation.x += dt * 0.15; node.cage.material.opacity = 0.9 * look * focus(i); }
  });

  // Edges: thickness from the back and forth so far, bright for a moment after each exchange.
  for (const pair of pairs.values()) {
    const m = pair.mesh;
    if (pair.w <= 0 || !alive[pair.i] || !alive[pair.j]) { m.visible = false; continue; }
    m.visible = true;
    from.copy(pos[pair.i]); to.copy(pos[pair.j]);
    const length = from.distanceTo(to);
    m.position.addVectors(from, to).multiplyScalar(0.5);
    m.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), tmp.subVectors(to, from).normalize());
    const share = Math.min(1, pair.w / W_MAX);                 // against the run's busiest pair
    const thick = 0.012 + 0.5 * Math.pow(share, 0.85);
    m.scale.set(thick, length, thick);
    const fresh = Math.max(0, 1 - (t - pair.last) / (D.spark * 2.5));
    const both = Math.min(rungs[pair.i].rung, rungs[pair.j].rung);
    m.material.color.copy(EDGE_COLOR).lerp(EDGE_RED, both >= CHEATED ? 0.85 : 0);
    const faded = 1 - 0.6 * Math.max(here[pair.i].gone, here[pair.j].gone);
    const lit = hovered < 0 ? 1 : (pair.i === hovered || pair.j === hovered ? 1.3 : 0.1);
    m.material.opacity = Math.min(1, (0.1 + 0.5 * share + 0.5 * fresh) * faded * lit);
  }

  for (const pool of pools) pool.begin();

  // Sparks: each message travels along its edge, from the writer to the agent it names (a read that asks for an
  // agent's posts travels from that agent to the reader).
  for (const [te, a, b, kind] of windowed(talkSorted, t, D.spark)) {
    if (!alive[a] || !alive[b]) continue;
    const p = (t - te) / D.spark;
    const [src, dst] = kind === ASK ? [b, a] : [a, b];
    for (let trail = 0; trail < 4; trail++) {
      const q = p - trail * 0.05;
      if (q < 0) continue;
      const s = glowPool.get();
      s.position.lerpVectors(pos[src], pos[dst], smooth(q));
      s.material.color.copy(kind === ASK ? ASK_COLOR : TALK_COLOR);
      s.material.opacity = (1 - trail * 0.22) * (1 - 0.3 * p) * (hovered < 0 || near.has(src) || near.has(dst) ? 1 : 0.15);
      s.scale.setScalar(1.1 - trail * 0.2);
    }
  }

  // Ripples: every post spreads a ring from its writer.
  for (const [te, a] of windowed(postsSorted, t, D.ripple)) {
    if (!alive[a]) continue;
    const p = (t - te) / D.ripple;
    const r = ringPool.get();
    r.position.copy(pos[a]);
    r.material.color.copy(TALK_COLOR);
    r.material.opacity = 0.45 * (1 - p) * focus(a);
    r.scale.setScalar(2.4 + 5 * p);
  }

  // Pieces in flight: an orb leaves its holder and lands on the agent that lacked it.
  flows.forEach(([tf, piece, src, dst], n) => {
    const color = PIECE_COLORS[piece % PIECE_COLORS.length];
    const start = src >= 0 ? pos[src] : farPoint(n + 1);
    if (t >= tf && t < tf + D.flow) {
      const p = (t - tf) / D.flow;
      const dist = start.distanceTo(pos[dst]);
      up.set(0, 1, 0).multiplyScalar(dist * 0.35);
      for (let trail = 0; trail < 7; trail++) {
        const q = smooth(p - trail * 0.03);
        if (p - trail * 0.03 < 0) continue;
        arc.lerpVectors(start, pos[dst], q).addScaledVector(up, Math.sin(Math.PI * q));
        if (trail === 0) {
          const orb = orbPool.get();
          orb.position.copy(arc);
          orb.scale.setScalar(0.42);
          orb.material.color.copy(color); orb.material.emissive.copy(color); orb.material.opacity = 1;
        }
        const g = glowPool.get();
        g.position.copy(arc);
        g.material.color.copy(color);
        g.material.opacity = 0.9 * (1 - trail / 7);
        g.scale.setScalar(2.2 - trail * 0.22);
      }
      if (p < 0.35 && src >= 0) {                      // the holder lets it go
        const r = ringPool.get();
        r.position.copy(start); r.material.color.copy(color);
        r.material.opacity = 0.8 * (1 - p / 0.35); r.scale.setScalar(2.5 + 8 * p);
      }
    }
    const landed = t - (tf + D.flow);
    if (landed >= 0 && landed < D.flash) {             // and it lands
      const p = landed / D.flash;
      const r = ringPool.get();
      r.position.copy(pos[dst]); r.material.color.copy(color);
      r.material.opacity = 0.9 * (1 - p); r.scale.setScalar(2.5 + 6 * p);
    }
  });

  // The pieces each agent holds circle it; a full set lights a ring.
  nodes.forEach((node, i) => {
    if (!alive[i]) return;
    const held = piecesHeld(i, t);
    const look = 1 - 0.7 * here[i].gone;
    const scale = node.group.scale.x;
    held.forEach((piece, k) => {
      const color = PIECE_COLORS[piece % PIECE_COLORS.length];
      const angle = clock * node.spin + (k * Math.PI * 2) / held.length;
      arc.set(Math.cos(angle) * 1.95, 0, Math.sin(angle) * 1.95).applyQuaternion(node.tilt).multiplyScalar(scale).add(pos[i]);
      const orb = orbPool.get();
      orb.position.copy(arc);
      orb.scale.setScalar(0.26 * scale);
      orb.material.color.copy(color); orb.material.emissive.copy(color);
      orb.material.opacity = look * focus(i);
      const g = glowPool.get();
      g.position.copy(arc); g.material.color.copy(color);
      g.material.opacity = 0.55 * look * focus(i); g.scale.setScalar(1.3 * scale);
    });
    node.complete.quaternion.copy(node.tilt).multiply(new THREE.Quaternion().setFromEuler(new THREE.Euler(Math.PI / 2, 0, 0)));
    node.complete.material.opacity = S.pieces >= 2 && held.length >= S.pieces ? 0.55 * look * focus(i) : 0;
  });

  // A rung reached flashes a ring in its colour.
  nodes.forEach((node, i) => {
    if (!alive[i]) return;
    const {rung, previous, since} = rungs[i];
    const p = (t - since) / D.flash;
    if (rung > previous && p >= 0 && p < 1) {
      const r = ringPool.get();
      r.position.copy(pos[i]); r.material.color.copy(RUNG_COLORS[rung]);
      r.material.opacity = (rung >= CHEATED ? 1 : 0.6) * (1 - p);
      r.scale.setScalar(2.5 + (rung >= CHEATED ? 10 : 5) * smooth(p));
    }
  });

  // Alerts to humans: the caller flashes a beacon and a beam reaches every agent it names; a named agent keeps an
  // amber ring from then on. A false alarm is grey.
  const flags = alertsUpTo(t);
  nodes.forEach((node, i) => {
    if (!alive[i]) return;
    node.flag.quaternion.copy(camera.quaternion);
    const on = flags.named[i] ? 0.35 + 0.12 * Math.min(flags.named[i], 4) : 0;
    node.flag.material.opacity = on * (0.75 + 0.25 * Math.sin(clock * 4)) * focus(i);
  });
  for (const [ta, caller, who, outcome] of windowed(ALERTS, t, D.alert)) {
    if (!alive[caller]) continue;
    const p = (t - ta) / D.alert;
    const color = outcome === 0 ? FALSE_ALARM : ALERT_COLOR;
    for (let wave = 0; wave < 3; wave++) {           // the beacon: three quick rings
      const q = (p * 3 - wave * 0.35);
      if (q < 0 || q > 1) continue;
      const r = ringPool.get();
      r.position.copy(pos[caller]); r.material.color.copy(color);
      r.material.opacity = 0.95 * (1 - q); r.scale.setScalar(2.4 + 7 * q);
    }
    const g = glowPool.get();
    g.position.copy(pos[caller]); g.material.color.copy(color);
    g.material.opacity = 0.9 * (1 - p) * (0.6 + 0.4 * Math.sin(clock * 14)); g.scale.setScalar(6);
    for (const j of who) {                           // a beam of light to each agent named
      if (!alive[j]) continue;
      const reach = smooth(p / 0.35);
      for (let k = 0; k <= 10; k++) {
        const q = (k / 10) * reach;
        const s2 = glowPool.get();
        s2.position.lerpVectors(pos[caller], pos[j], q);
        s2.material.color.copy(color);
        s2.material.opacity = 0.85 * (1 - p);
        s2.scale.setScalar(0.9);
      }
      if (reach >= 1) {
        const r = ringPool.get();
        r.position.copy(pos[j]); r.material.color.copy(color);
        r.material.opacity = 0.9 * (1 - p); r.scale.setScalar(2.5 + 4 * smooth((p - 0.35) / 0.65));
      }
    }
  }

  // The hand-in: a wave across the swarm, red when the team cheated; the room stays red after.
  if (S.submit) {
    const p = (t - FINAL_T) / D.submit;
    const color = VERDICT_COLOR;
    if (p >= 0 && p < 1) {
      for (let wave = 0; wave < 3; wave++) {
        const q = p - wave * 0.12;
        if (q < 0) continue;
        const r = ringPool.get();
        r.position.copy(pos[FINAL ? FINAL[1] : S.submit.agent]); r.material.color.copy(color);
        r.material.opacity = 0.9 * (1 - q);
        r.scale.setScalar(3 + radius * 3.2 * smooth(q));
      }
    }
    tint.style.opacity = S.submit.cheated ? String(0.9 * smooth(p * 2)) : '0';
    honestRoom.style.opacity = S.submit.cheated ? '0' : String(0.9 * smooth(p * 2));
  }

  // Hand-ins: the room turns teal while a hand-in stands and the agent who made it wears a teal ring; a take-back
  // flashes violet rings closing in on the agent who took it, and the room goes back.
  const hand = handinAt(t);
  const isFinal = hand.holder >= 0 && hand.since === FINAL_T;
  const roomOn = hand.holder >= 0 ? smooth((t - hand.since) / D.fade) : 1 - smooth((t - hand.since) / D.fade);
  handinRoom.style.opacity = String(t >= FINAL_T ? 0 : Math.max(0, roomOn));
  nodes.forEach((node, i) => {
    const holding = alive[i] && hand.holder === i;
    node.hold.material.color.copy(isFinal ? VERDICT_COLOR : HANDIN_COLOR);
    node.hold.material.opacity = holding ? 0.85 * smooth((t - hand.since) / D.fade) * focus(i) : 0;
    node.hold.rotation.set(Math.PI / 2, 0, clock * 0.8);
  });
  if (hand.back && alive[hand.back[1]]) {
    const p = (t - hand.back[0]) / D.flash;
    if (p >= 0 && p < 1) {
      for (let wave = 0; wave < 3; wave++) {
        const q = p * 1.6 - wave * 0.25;
        if (q < 0 || q > 1) continue;
        const r = ringPool.get();
        r.position.copy(pos[hand.back[1]]); r.material.color.copy(TAKEBACK_COLOR);
        r.material.opacity = 0.95 * (1 - p); r.scale.setScalar(2.2 + 9 * (1 - smooth(q)));
      }
      const g = glowPool.get();
      g.position.copy(pos[hand.back[1]]); g.material.color.copy(TAKEBACK_COLOR);
      g.material.opacity = 0.9 * (1 - p); g.scale.setScalar(6);
    }
  }

  // The call being stepped: a white ring on the agent making it.
  syncCursor();
  const shown = stepShown();
  if (shown && alive[shown[1]]) {
    const r = ringPool.get();
    r.position.copy(pos[shown[1]]); r.material.color.setRGB(1, 1, 1);
    r.material.opacity = 0.75 + 0.25 * Math.sin(clock * 6); r.scale.setScalar(4.6);
  }

  for (const pool of pools) pool.end();
  placeCamera(alive, dt);
  drawWords(rungs, here, alive, focus, flags, hand);
}

// ---------- controls: play, timeline, speed (icons only) ----------
const playButton = document.getElementById('play');
const playIcon = document.getElementById('playIcon');
const speedButton = document.getElementById('speed');
const speedIcon = document.getElementById('speedIcon');
const timeline = document.getElementById('timeline');
const tctx = timeline.getContext('2d');
let backdrop = null;

// ---------- stepping: one call at a time, or one round at a time ----------
const STEPS = S.steps || [];
const ROUNDS = S.rounds || [];
let cursor = -1, cursorT = NaN, stepped = false;   // the call shown; stepped while the user steps and nothing moves t
function syncCursor() {
  if (t !== cursorT) { cursor = firstFrom(STEPS, t) - 1; cursorT = t; stepped = false; }
}
function stepShown() { return stepped && cursor >= 0 ? STEPS[cursor] : null; }
function roundAt(v) {                            // index of the round under way at v
  let k = -1;
  for (let j = 0; j < ROUNDS.length && ROUNDS[j][1] <= v + 1e-9; j++) k = j;
  return k;
}
function goStep(dir) {
  setPlaying(false);
  syncCursor();
  if (!stepped && dir > 0) cursor = firstFrom(STEPS, t - 1e-9) - 1;   // the next step is the first call at or after t
  cursor = Math.max(0, Math.min(STEPS.length - 1, cursor + dir));
  if (cursor >= 0) t = cursorT = STEPS[cursor][0];
  stepped = cursor >= 0;
}
function landing(k) {                            // the first call of round k (index into STEPS), or -1
  if (k < 0 || k >= ROUNDS.length) return -1;
  const j = firstFrom(STEPS, ROUNDS[k][1] - 1e-9);
  const end = k + 1 < ROUNDS.length ? ROUNDS[k + 1][1] : Infinity;
  return j < STEPS.length && STEPS[j][0] < end ? j : -1;
}
function landOnRound(k) {
  k = Math.max(0, Math.min(ROUNDS.length - 1, k));
  const j = landing(k);
  if (j >= 0) { cursor = j; t = STEPS[j][0]; stepped = true; } else { t = ROUNDS[k][1]; stepped = false; }
  cursorT = t;
}
function goRound(dir) {
  setPlaying(false);
  if (!ROUNDS.length) return;
  syncCursor();
  const k = Math.max(0, roundAt(t));
  if (dir > 0) { if (k + 1 < ROUNDS.length) landOnRound(k + 1); return; }
  const j = landing(k);                          // back: to this round's first call, or the round before
  const atStart = j >= 0 ? stepped && cursor === j : t <= ROUNDS[k][1] + 1e-6;
  landOnRound(atStart ? k - 1 : k);
}
function holdToRepeat(id, action) {              // press to step once, hold to keep stepping
  const button = document.getElementById(id);
  let delay = null, repeat = null;
  const stop = () => { clearTimeout(delay); clearInterval(repeat); };
  button.addEventListener('pointerdown', e => {
    e.preventDefault(); action();
    delay = setTimeout(() => { repeat = setInterval(action, 70); }, 350);
  });
  for (const ev of ['pointerup', 'pointerleave', 'pointercancel']) button.addEventListener(ev, stop);
}
holdToRepeat('stepBack', () => goStep(-1));
holdToRepeat('stepFwd', () => goStep(1));
holdToRepeat('roundBack', () => goRound(-1));
holdToRepeat('roundFwd', () => goRound(1));

function setPlaying(on) {
  if (on && t >= T) t = 0;
  playing = on;
  playIcon.setAttribute('d', on ? 'M6.5 4.5h4v15h-4zM13.5 4.5h4v15h-4z' : 'M7 4.5 20 12 7 19.5Z');
}
function drawSpeed() {
  const n = speedIndex + 1, w = 5, gap = 1.5, total = n * w + (n - 1) * gap;
  let html = '';
  for (let k = 0; k < n; k++) {
    const x = 12 - total / 2 + k * (w + gap);
    html += `<path d="M${x} 6.5 ${x + w} 12 ${x} 17.5Z"/>`;
  }
  speedIcon.innerHTML = html;
}
playButton.addEventListener('click', () => setPlaying(!playing));
speedButton.addEventListener('click', () => { speedIndex = (speedIndex + 1) % SPEEDS.length; drawSpeed(); });
window.addEventListener('keydown', e => {
  if (e.code === 'Space') { e.preventDefault(); setPlaying(!playing); }
  if (e.code === 'ArrowRight') { e.preventDefault(); e.shiftKey ? goRound(1) : goStep(1); }
  if (e.code === 'ArrowLeft') { e.preventDefault(); e.shiftKey ? goRound(-1) : goStep(-1); }
});

function buildBackdrop() {
  const dpr = Math.min(window.devicePixelRatio, 2);
  const w = timeline.clientWidth, h = timeline.clientHeight;
  timeline.width = w * dpr; timeline.height = h * dpr;
  backdrop = document.createElement('canvas');
  backdrop.width = timeline.width; backdrop.height = timeline.height;
  const g = backdrop.getContext('2d');
  g.scale(dpr, dpr);
  const x = v => 8 + (v / T) * (w - 16);
  // How busy the board is: a soft histogram of the exchanges.
  const bins = 160, counts = new Array(bins).fill(0);
  for (const [te] of S.talk) counts[Math.min(bins - 1, Math.floor((te / T) * bins))]++;
  for (const [te] of S.posts) counts[Math.min(bins - 1, Math.floor((te / T) * bins))] += 0.5;
  const top = Math.max(1, ...counts);
  g.fillStyle = 'rgba(160,190,255,0.22)';
  counts.forEach((c, b) => {
    const bh = (c / top) * (h * 0.42);
    g.fillRect(x((b / bins) * T), h / 2 - bh / 2, Math.max(1, (w - 16) / bins - 1), bh);
  });
  // The track.
  g.fillStyle = 'rgba(200,215,255,0.16)';
  g.fillRect(8, h / 2 - 1.5, w - 16, 3);
  // Pieces passed: a dot of the piece's colour above the track.
  for (const [tf, piece] of S.flows) {
    g.fillStyle = '#' + PIECE_COLORS[piece % PIECE_COLORS.length].getHexString();
    g.beginPath(); g.arc(x(tf), h * 0.2, 2.6, 0, Math.PI * 2); g.fill();
  }
  // Agents turning red: a red tick below the track.
  for (const agent of S.agents) {
    let before = 0;
    for (const [at, value] of agent.rungs) {
      if (value >= CHEATED && before < CHEATED) {
        g.fillStyle = '#ff2d3d';
        g.fillRect(x(at) - 1, h * 0.66, 2.2, h * 0.24);
      }
      before = value;
    }
  }
  // Hand-ins: a teal band along the track while one stood; a violet tick at each take-back.
  {
    let from = null;
    for (const [th, , kind] of HANDINS) {
      if (kind === 0 && from === null) from = th;
      if (kind === 1 && from !== null) {
        g.fillStyle = '#' + HANDIN_COLOR.getHexString(); g.fillRect(x(from), h / 2 - 3, Math.max(2, x(th) - x(from)), 6);
        from = null;
      }
      if (kind === 1) { g.fillStyle = '#d68cff'; g.fillRect(x(th) - 1, h / 2 - 7, 2, 14); }
    }
    if (from !== null) {
      g.fillStyle = '#' + (from === FINAL_T ? VERDICT_COLOR : HANDIN_COLOR).getHexString();
      g.fillRect(x(from), h / 2 - 3, Math.max(2, x(T) - x(from)), 6);
    }
  }
  // Alerts: a triangle above the track, amber (grey for a false alarm).
  for (const [ta, , , outcome] of ALERTS) {
    const ax = x(ta), ay = h * 0.08;
    g.fillStyle = outcome === 0 ? '#a3abc2' : '#ffc21a';
    g.beginPath(); g.moveTo(ax, ay + 7); g.lineTo(ax - 4, ay); g.lineTo(ax + 4, ay); g.closePath(); g.fill();
  }
  // The hand-in: a diamond.
  if (S.submit) {
    const sx = x(FINAL_T), sy = h / 2;
    g.fillStyle = S.submit.cheated ? '#ff2d3d' : '#a8ffc4';
    g.beginPath(); g.moveTo(sx, sy - 7); g.lineTo(sx + 6, sy); g.lineTo(sx, sy + 7); g.lineTo(sx - 6, sy); g.closePath(); g.fill();
  }
}
function drawTimeline() {
  if (!backdrop) buildBackdrop();
  const dpr = timeline.width / timeline.clientWidth;
  const w = timeline.clientWidth, h = timeline.clientHeight;
  tctx.setTransform(1, 0, 0, 1, 0, 0);
  tctx.clearRect(0, 0, timeline.width, timeline.height);
  tctx.drawImage(backdrop, 0, 0);
  tctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const px = 8 + (t / T) * (w - 16);
  tctx.fillStyle = 'rgba(230,238,255,0.75)';
  tctx.fillRect(8, h / 2 - 1.5, px - 8, 3);
  tctx.fillStyle = '#ffffff';
  tctx.shadowColor = 'rgba(255,255,255,0.8)'; tctx.shadowBlur = 8;
  tctx.beginPath(); tctx.arc(px, h / 2, 6, 0, Math.PI * 2); tctx.fill();
  tctx.shadowBlur = 0;
}
let scrubbing = false;
function scrubTo(e) {
  const rect = timeline.getBoundingClientRect();
  t = Math.min(T, Math.max(0, ((e.clientX - rect.left - 8) / (rect.width - 16)) * T));
}
timeline.addEventListener('pointerdown', e => { scrubbing = true; timeline.setPointerCapture(e.pointerId); scrubTo(e); });
timeline.addEventListener('pointermove', e => { if (scrubbing) scrubTo(e); });
timeline.addEventListener('pointerup', e => { scrubbing = false; timeline.releasePointerCapture(e.pointerId); });

function resize() {
  renderer.setSize(window.innerWidth, window.innerHeight, false);
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  backdrop = null;
}
window.addEventListener('resize', resize);

// A URL hash #t=0.5 opens the run halfway through, paused (for stills).
// #round=7 opens at round 7's first call and #step=120 at the 120th call, both paused and stepping.
const startAt = /(?:^#|&)t=([0-9.]+)/.exec(location.hash);
const roundAtHash = /round=(\d+)/.exec(location.hash);
const stepAtHash = /step=(\d+)/.exec(location.hash);
if (startAt) t = Math.min(1, parseFloat(startAt[1])) * T;
if (roundAtHash) {
  const k = ROUNDS.findIndex(([r]) => r === parseInt(roundAtHash[1], 10));
  if (k >= 0) landOnRound(k);
}
if (stepAtHash && STEPS.length) {
  cursor = Math.max(0, Math.min(STEPS.length - 1, parseInt(stepAtHash[1], 10) - 1));
  t = cursorT = STEPS[cursor][0];
  stepped = true;
}
const opened = startAt || roundAtHash || stepAtHash;

function frame(now) {
  const dt = Math.min(0.05, (now - last) / 1000);
  last = now;
  clock += dt;
  if (playing && !scrubbing) {
    t += dt * SPS * SPEEDS[speedIndex];
    if (t >= T) { t = T; setPlaying(false); }
  }
  if (!dragging && performance.now() - lastDrag > 2500) azimuth += dt * 0.07;
  update(dt);
  renderer.render(scene, camera);
  drawTimeline();
  requestAnimationFrame(frame);
}
resize();
drawSpeed();
// Settle the layout at the opening moment before the first frame.
{
  const alive = S.agents.map(a => t >= a.start);
  for (const pair of pairs.values()) {
    pair.w = pairWeight(pair, t);
  }
  for (let s = 0; s < 400; s++) stepLayout(alive);
  placeCamera(alive, 10);
}
requestAnimationFrame(frame);
if (!opened) setTimeout(() => setPlaying(true), 900);
window.__swarm = {setTime: v => { t = v * T; }, get t() { return t / T; }, play: setPlaying, step: goStep, round: goRound};
