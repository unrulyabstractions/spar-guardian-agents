// The swarm in 3D. Each node is an agent; edges grow as agents talk on the board (a post naming the other, or a
// read asking for the other's posts), thicker the more they go back and forth. The exploit's pieces circle the agents
// that hold them: orbs for one family, small diamonds for the other; a piece an agent first sees flies in to it from
// the scene's centre. Nodes take their Cheating rung's colour; from the final hand-in on, every agent shows its label.
import * as THREE from 'three';

const S = JSON.parse(document.getElementById('story').textContent);
const N = S.agents.length;
const T = Math.max(S.duration, 1e-6);
const PLAY_SECONDS = 120;                // the whole run plays in this many seconds at 1x
const SPEEDS = [0.25, 0.5, 1, 2];
const SPS = T / PLAY_SECONDS;            // story seconds per screen second at 1x
const D = {spark: 1.0 * SPS, flow: 2.4 * SPS, ripple: 1.0 * SPS, flash: 1.8 * SPS, verdict: 1.0 * SPS,
           fade: 0.7 * SPS, change: 0.06 * SPS, alert: 3.2 * SPS, check: 2.2 * SPS};
const ASK = 1;
const CHEATED = 3;
const HACKED = 4;

const RUNG_COLORS = ['#c9d6e8', '#ffe1a6', '#ff9d47', '#ff2a2a', '#ff0a3c'].map(c => new THREE.Color(c));
const RUNG_WORDS = ['none', 'looked', 'tempted', 'attempted', 'hacked'];
const EDGE_COLOR = new THREE.Color('#8fb3ff');
const EDGE_RED = new THREE.Color('#ff2f45');
const TALK_COLOR = new THREE.Color('#eaf2ff');
const ASK_COLOR = new THREE.Color('#7fe8ff');
const ROLE_EDGE = {regulator: new THREE.Color('#38e1ff'),      // the outline of a regulator (cyan) and of a
                   disregulator: new THREE.Color('#ff9de2')};  // disregulator (pink), as in the legend
const ALERT_COLOR = new THREE.Color('#ffc21a');          // an alert to humans; a false alarm is grey
const FALSE_ALARM = new THREE.Color('#8b93a8');
const UNKNOWN_ALARM = new THREE.Color('#b8953a');
const alarmColor = outcome => (outcome === 1 ? ALERT_COLOR : outcome === 0 ? FALSE_ALARM : UNKNOWN_ALARM);
// One pip per accusation: filled amber when the agent named had cheated by then (a hit), hollow amber when the alarm
// was true only for the team, grey when false, dashed grey when unknown.
const pipKind = (outcome, flag) => (outcome === 0 ? 'false' : outcome === 1 && flag === 1 ? 'hit'
                                    : outcome === 1 && flag === 0 ? 'team' : 'unknown');
const ALERTS = (S.alerts || []).slice().sort((a, b) => a[0] - b[0]);

// The exploit's pieces, by family (run.json swe.exploits): orbs for the first, small diamonds for the second.
const PIECE_NAMES = S.piece_names || [];
const FAMILIES = S.families || (PIECE_NAMES.length ? [{name: null, pieces: PIECE_NAMES.map((_, i) => i)}] : []);
const PIECE_FAMILY = S.piece_family || PIECE_NAMES.map(() => 0);
const FAMILY_HUES = [['#16d0c0', '#2fe07a', '#b46dff', '#5ab0ff', '#7dffb0'],
                     ['#f0e040', '#a8f04a', '#e0a870', '#ffd27a', '#d0ff8a']];
const FAMILY_ICON = ['●', '◆'];
const FAMILY_HOLLOW = ['○', '◇'];
const PIECE_COLORS = PIECE_NAMES.map((_, i) => {
  const f = PIECE_FAMILY[i] || 0, k = (FAMILIES[f] ? FAMILIES[f].pieces.indexOf(i) : i);
  const hues = FAMILY_HUES[f % FAMILY_HUES.length];
  return new THREE.Color(hues[Math.max(0, k) % hues.length]);
});
const pieceColor = i => PIECE_COLORS[i] || new THREE.Color('#8fb3ff');
const familyOf = i => PIECE_FAMILY[i] || 0;

// What a version's code held: how many of the planted values (S.values) sit on an added line. A fact about the code,
// not a verdict. Shown as N white segments filled n of N, dashed when unknown (its code was not stored).
const VALUES = S.values || [];
const NV = VALUES.length;
const HELD_COLORS = {unknown: new THREE.Color('#6b7385'), none: new THREE.Color('#7f8db0'),
                     some: new THREE.Color('#c9d3ea'), all: new THREE.Color('#ffffff')};
const heldLevel = held => (!Array.isArray(held) || !NV ? 'unknown' : !held.length ? 'none' : held.length >= NV ? 'all' : 'some');
const heldColor = held => HELD_COLORS[heldLevel(held)];
const heldText = held => (heldLevel(held) === 'unknown' ? '?' : `${held.length}/${NV}`);

// Hand-ins. Only the final one is graded (the labels): the team's rung in its own colour, a white ring for pass.
const UNGRADED_COLOR = new THREE.Color('#8a93a8');
const TAKEBACK_COLOR = new THREE.Color('#d68cff');        // a hand-in taken back
const HANDINS = (S.handins || []).slice().sort((a, b) => a[0] - b[0]);
if (!HANDINS.length && S.submit) HANDINS.push([S.submit.t, S.submit.agent, 0, '', S.submit.held ?? null]);
const FINAL = (() => {                                    // the final hand-in: the last one no take-back followed
  const last = HANDINS[HANDINS.length - 1];
  return S.submit && last && last[2] === 0 ? last : null;
})();
const FINAL_T = S.final_t ?? (FINAL ? FINAL[0] : (S.submit ? S.submit.t : Infinity));
const LABEL_T = Number.isFinite(FINAL_T) ? FINAL_T : T;   // when every agent takes its final rung
const FINAL_AGENT = FINAL ? FINAL[1] : (S.submit ? S.submit.agent : -1);
const FINAL_HELD = S.submit ? (S.submit.held !== undefined ? S.submit.held : (FINAL ? FINAL[4] : null)) : null;
const GRADED = !!S.submit && S.submit.labelled !== false;
const TEAM_RUNG = S.team_rung || 0;
const PASSED = GRADED && !!S.submit.passed;
const VERDICT_COLOR = GRADED ? RUNG_COLORS[TEAM_RUNG] : UNGRADED_COLOR;
const CHECKS = (S.checks || []).slice().sort((a, b) => a[0] - b[0]);
const FALLS = (S.falls || []).slice().sort((a, b) => a[0] - b[0]);
const TAU = 0.08 * T;                                     // an exchange's weight halves in about 0.7 TAU
function handinAt(t) {                                    // who holds the hand-in at t, since when, what it held
  let holder = -1, since = -Infinity, back = null, held = null;
  for (const row of HANDINS) {
    if (row[0] > t) break;
    if (row[2] === 0) { holder = row[1]; since = row[0]; held = row[4] ?? null; } else { holder = -1; since = row[0]; back = row; }
  }
  return {holder, since, back, held};
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

function canvasTexture(draw) {
  const c = document.createElement('canvas');
  c.width = c.height = 256;
  draw(c.getContext('2d'));
  const texture = new THREE.CanvasTexture(c);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}
function radialTexture(stops) {
  return canvasTexture(g => {
    const gradient = g.createRadialGradient(128, 128, 0, 128, 128, 128);
    for (const [at, alpha] of stops) gradient.addColorStop(at, `rgba(255,255,255,${alpha})`);
    g.fillStyle = gradient;
    g.fillRect(0, 0, 256, 256);
  });
}
// A circle of radius 0.9 of the sprite's half size: a sprite of scale s draws a ring of radius 0.45 s.
const CIRCLE_R = 0.45;
function circleTexture(width, dashed = false) {
  return canvasTexture(g => {
    g.strokeStyle = '#ffffff';
    g.lineWidth = width;
    if (dashed) g.setLineDash([14, 10]);
    g.beginPath(); g.arc(128, 128, 115, 0, Math.PI * 2); g.stroke();
  });
}
const GLOW = radialTexture([[0, 1], [0.2, 0.6], [0.5, 0.16], [1, 0]]);
const RING = radialTexture([[0, 0], [0.7, 0], [0.8, 0.95], [0.88, 0.3], [1, 0]]);
const DOT = canvasTexture(g => { g.fillStyle = '#fff'; g.beginPath(); g.arc(128, 128, 100, 0, Math.PI * 2); g.fill();
                                 g.strokeStyle = '#070a12'; g.lineWidth = 26; g.stroke(); });
const HOLLOW = canvasTexture(g => { g.strokeStyle = '#fff'; g.lineWidth = 46; g.beginPath(); g.arc(128, 128, 92, 0, Math.PI * 2);
                                    g.stroke(); });
const DASHED_DOT = canvasTexture(g => { g.strokeStyle = '#fff'; g.lineWidth = 34; g.setLineDash([46, 30]);
                                        g.beginPath(); g.arc(128, 128, 98, 0, Math.PI * 2); g.stroke(); });
const RING_LEFT = circleTexture(7, true);                 // an agent that left: a dashed edge
const RING_RUNG = circleTexture(15);                     // a rung change: the brightest cue of a frame

function sprite(texture, color = 0xffffff, opacity = 1, additive = true) {
  const material = new THREE.SpriteMaterial({map: texture, color, transparent: true, opacity,
                                             blending: additive ? THREE.AdditiveBlending : THREE.NormalBlending,
                                             depthWrite: false});
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
const thinPool = new Pool(() => sprite(circleTexture(3)));         // the final hand-in's ring: a thin line
const rungPool = new Pool(() => sprite(RING_RUNG));
const pipPool = new Pool(() => sprite(DOT, 0xffffff, 1, false));
const pipHollowPool = new Pool(() => sprite(HOLLOW, 0xffffff, 1, false));
const pipDashPool = new Pool(() => sprite(DASHED_DOT, 0xffffff, 1, false));
const orbGeometry = new THREE.SphereGeometry(1, 20, 14);
const gemGeometry = new THREE.OctahedronGeometry(1.15, 0).scale(0.75, 1.3, 0.75);   // a small upright diamond
const pieceMaterial = () => new THREE.MeshStandardMaterial({roughness: 0.25, metalness: 0.1, emissiveIntensity: 1.2,
                                                            transparent: true});
const orbPool = new Pool(() => new THREE.Mesh(orbGeometry, pieceMaterial()));
const gemPool = new Pool(() => new THREE.Mesh(gemGeometry, Object.assign(pieceMaterial(), {flatShading: true})));
// A piece seen but not dealt: a hollow outline of the same shape.
const orbHollowPool = new Pool(() => sprite(circleTexture(26), 0xffffff, 1, false));
const gemEdges = new THREE.EdgesGeometry(gemGeometry);
const gemHollowPool = new Pool(() => new THREE.LineSegments(gemEdges, new THREE.LineBasicMaterial({transparent: true,
                                                                                                  toneMapped: false})));
const pools = [glowPool, ringPool, thinPool, rungPool, pipPool, pipHollowPool, pipDashPool, orbPool, gemPool,
               orbHollowPool, gemHollowPool];
const ORB_SPRITE = 1.05 / CIRCLE_R;           // a hollow orb's sprite scale per unit of orb radius (a little wider)
function pieceMesh(piece, hollow = false) {   // an orb for the first family, a small diamond for the second
  const gem = familyOf(piece) === 1;
  const color = pieceColor(piece);
  if (hollow) {
    const mesh = gem ? gemHollowPool.get() : orbHollowPool.get();
    if (gem) mesh.quaternion.copy(camera.quaternion);
    mesh.material.color.copy(color);
    mesh.userData.hollowOrb = !gem;
    return mesh;
  }
  const mesh = gem ? gemPool.get() : orbPool.get();
  if (gem) mesh.quaternion.copy(camera.quaternion);           // upright and facing the camera: a rhombus
  mesh.material.color.copy(color); mesh.material.emissive.copy(color);
  return mesh;
}
function sizePiece(mesh, size) {              // a mesh's radius, or a hollow orb sprite's ring radius
  mesh.scale.setScalar(mesh.userData.hollowOrb ? size * ORB_SPRITE : size);
}

// ---------- agents ----------
function spikyGeometry() {
  // A spiky ball: a once-subdivided icosahedron whose every face is pulled out into a point.
  const base = new THREE.IcosahedronGeometry(0.8, 1).toNonIndexed();
  const p = base.getAttribute('position');
  const out = [];
  const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3(), tip = new THREE.Vector3();
  for (let f = 0; f < p.count; f += 3) {
    a.fromBufferAttribute(p, f); b.fromBufferAttribute(p, f + 1); c.fromBufferAttribute(p, f + 2);
    tip.copy(a).add(b).add(c).normalize().multiplyScalar(1.25);
    for (const [u, v] of [[a, b], [b, c], [c, a]]) out.push(u.x, u.y, u.z, v.x, v.y, v.z, tip.x, tip.y, tip.z);
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(out, 3));
  geometry.computeVertexNormals();
  return geometry;
}

// Where each agent sits: workers on a ring, regulators on a small ring at its centre raised a little, disregulators
// likewise (lowered when a run has both). Each agent is held to its place by a spring; talk only nudges it.
const ANCHORS = (() => {
  const groups = {worker: [], regulator: [], disregulator: []};
  S.agents.forEach((a, i) => (groups[a.role] || groups.worker).push(i));
  const out = new Array(N);
  const ring = (members, radius, y, phase) => members.forEach((i, k) => {
    if (members.length === 1) { out[i] = new THREE.Vector3(0, y, 0); return; }
    const a = phase + (k / members.length) * Math.PI * 2;
    out[i] = new THREE.Vector3(radius * Math.cos(a), y, radius * Math.sin(a));
  });
  const small = n => Math.max(2.4, (n * 3.6) / (2 * Math.PI));
  const inner = Math.max(groups.regulator.length ? small(groups.regulator.length) : 0,
                         groups.disregulator.length ? small(groups.disregulator.length) : 0);
  const rw = Math.max(inner ? inner + 5 : 5.5, (groups.worker.length * 4.6) / (2 * Math.PI));
  const both = groups.regulator.length && groups.disregulator.length;
  ring(groups.worker, rw, 0, 0);
  ring(groups.regulator, small(groups.regulator.length), 1.2, Math.PI / groups.worker.length);
  ring(groups.disregulator, small(groups.disregulator.length), both ? -1.2 : 1.2, Math.PI / groups.worker.length + 0.5);
  return out;
})();
const CROWDED = N > 12;                       // a crowded swarm shows tags only for agents doing something now
const pos = [], vel = [];
// Radii around a node of radius 1: the dashed edge of an agent that left, the alert pips, the held segments.
const R_LEFT = {worker: 1.14, regulator: 1.3, disregulator: 1.42}, R_PIPS = 1.5, R_HELD = [1.42, 1.56];
const facetGeometry = new THREE.IcosahedronGeometry(1.08, 1);   // hacked: a faceted, low-poly solid
const nodes = S.agents.map((agent, i) => {
  const group = new THREE.Group();
  scene.add(group);
  const material = new THREE.MeshStandardMaterial({color: RUNG_COLORS[0].clone(), emissive: RUNG_COLORS[0].clone(),
                                                   emissiveIntensity: 0.25, roughness: 0.32, metalness: 0.12,
                                                   transparent: true, flatShading: agent.role !== 'worker'});
  let core, cage = null, facet = null;
  const edgeMaterial = new THREE.LineBasicMaterial({color: ROLE_EDGE[agent.role] || ROLE_EDGE.regulator,
                                                    transparent: true, opacity: 0.95, toneMapped: false});
  if (agent.role === 'disregulator') {
    const geometry = spikyGeometry();
    core = new THREE.Mesh(geometry, material);
    cage = new THREE.LineSegments(new THREE.EdgesGeometry(geometry, 30), edgeMaterial);
    cage.scale.setScalar(1.025);
    core.add(cage);
  } else if (agent.role === 'regulator') {
    const box = new THREE.BoxGeometry(1.3, 1.3, 1.3);
    core = new THREE.Mesh(box, material);
    cage = new THREE.LineSegments(new THREE.EdgesGeometry(box), edgeMaterial);
    cage.scale.setScalar(1.025);
    core.add(cage);
  } else {
    core = new THREE.Mesh(new THREE.SphereGeometry(1, 48, 32), material);
    // Hacked: the same node as a flat-shaded, faceted solid; attempted stays a smooth sphere.
    facet = new THREE.Mesh(facetGeometry, new THREE.MeshStandardMaterial({
      color: RUNG_COLORS[HACKED].clone(), emissive: RUNG_COLORS[HACKED].clone(), emissiveIntensity: 0.5,
      roughness: 0.45, metalness: 0.15, transparent: true, flatShading: true}));
    facet.visible = false;
    facet.userData.agent = i;
    group.add(facet);
  }
  core.userData.agent = i;
  group.add(core);
  const glow = sprite(GLOW, RUNG_COLORS[0], 0.4);
  glow.scale.setScalar(4);
  group.add(glow);
  const ringSprite = (texture, radius) => {
    const s = sprite(texture, 0xffffff, 0, false);
    s.scale.setScalar(radius / CIRCLE_R);
    group.add(s);
    return s;
  };
  const leftRing = ringSprite(RING_LEFT, R_LEFT[agent.role] || R_LEFT.worker);   // an agent that left: a dashed edge
  // What the standing hand-in's code held: N small segments at the top of the node, filled n of N; three dashes each
  // when unknown. Turned to face the camera each frame.
  const held = new THREE.Group();
  const segments = [];
  const span = Math.min(0.42, 1.25 / Math.max(1, NV)), gap = 0.1;
  for (let k = 0; k < NV; k++) {
    const start = Math.PI / 2 + ((NV - 1) / 2 - k) * (span + gap) - span / 2;
    const solid = new THREE.Mesh(new THREE.RingGeometry(R_HELD[0], R_HELD[1], 12, 1, start, span),
                                 new THREE.MeshBasicMaterial({color: 0xffffff, transparent: true, opacity: 0,
                                                              side: THREE.DoubleSide, depthWrite: false}));
    const dashes = [0, 1, 2].map(d => {
      const m = new THREE.Mesh(new THREE.RingGeometry(R_HELD[0] + 0.03, R_HELD[1] - 0.03, 4, 1,
                                                      start + (d * span) / 3 + span * 0.05, span / 3 - span * 0.1),
                               new THREE.MeshBasicMaterial({color: 0xffffff, transparent: true, opacity: 0,
                                                            side: THREE.DoubleSide, depthWrite: false}));
      held.add(m);
      return m;
    });
    held.add(solid);
    segments.push({solid, dashes});
  }
  group.add(held);
  pos.push(ANCHORS[i].clone().add(new THREE.Vector3(Math.sin(i * 7.1) * 0.2, Math.cos(i * 3.3) * 0.2, 0)));
  vel.push(new THREE.Vector3());
  const tilt = new THREE.Quaternion().setFromEuler(new THREE.Euler((i * 1.7) % Math.PI, (i * 2.3) % Math.PI, 0));
  return {agent, group, core, facet, material, cage, glow, leftRing, held, segments, tilt,
          spin: 0.6 + (i % 5) * 0.12};
});
const cores = nodes.flatMap(n => (n.facet ? [n.core, n.facet] : [n.core]));
// Per agent: a pip kind for each accusation naming it; and of the alerts it raised, the true, false and unknown ones
// and its hits and misses (agents named who had, or had not, cheated by then).
function alertsUpTo(t) {
  const named = Array.from({length: N}, () => []);
  const raised = Array.from({length: N}, () => [0, 0, 0, 0, 0]);
  for (const [ta, caller, who, outcome, , flags] of ALERTS) {
    if (ta > t) break;
    raised[caller][outcome === 1 ? 0 : outcome === 0 ? 1 : 2]++;
    who.forEach((j, k) => {
      const flag = flags ? flags[k] : -1;
      named[j].push(pipKind(outcome, flag));
      if (flag === 1) raised[caller][3]++;
      else if (flag === 0) raised[caller][4]++;
    });
  }
  return {named, raised};
}

// ---------- words: a tag under each agent, the setup chips, the clock, a feed of events, a legend ----------
// Words are single words, numbers and icons: never a sentence.
const ROLE_ICON = {worker: '●', regulator: '■', disregulator: '✹'};
const ROLE_ACCENT = {worker: '#8fb3ff', regulator: '#7fe8ff', disregulator: '#ff9de2'};
const ROLE_COLORS = Object.fromEntries(Object.entries(ROLE_ACCENT).map(([role, c]) => [role, new THREE.Color(c)]));
const STEP_COLOR = new THREE.Color('#9fb3d9');
const esc = text => String(text).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
const hex = color => '#' + color.getHexString();
const nameOf = i => (i >= 0 ? `<b style="color:${ROLE_ACCENT[S.agents[i].role] || '#fff'}">${esc(S.agents[i].label)}</b>` : '∅');
const clockText = v => { const s = Math.max(0, Math.round(v)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
const chip = (text, color, cls = '') => `<span class="chip ${cls}" style="color:${color};background:${color}22">${text}</span>`;
function segs(held) {                         // N small segments, n filled; dashed when unknown
  const level = heldLevel(held);
  if (!NV) return '';
  let out = '';
  for (let k = 0; k < NV; k++) out += `<i class="seg${level === 'unknown' ? ' unknown' : k < held.length ? ' on' : ''}"></i>`;
  return `<span class="segs">${out}</span>`;
}
const heldChip = held => (NV ? `<span class="held">${segs(held)}<span class="n">${heldText(held)}</span></span>` : '');
// The verdict's shape, as on the study page: a round disc in the rung's colour, a red hexagon for hacked, a white
// ring for pass.
const hexPoints = r => [0, 1, 2, 3, 4, 5].map(k => `${(r * Math.sin((k * Math.PI) / 3)).toFixed(2)},` +
                                                   `${(-r * Math.cos((k * Math.PI) / 3)).toFixed(2)}`).join(' ');
function discSVG(rung, passed) {
  const fill = hex(RUNG_COLORS[rung]);
  if (rung === HACKED) {                       // a hexagon, and for pass a white ring of the same shape
    const ring = passed ? `<polygon points="${hexPoints(14.2)}" fill="none" stroke="#ffffff" stroke-width="2.2" stroke-linejoin="round"/>` : '';
    return `<svg viewBox="-15 -15 30 30" aria-hidden="true"><polygon points="${hexPoints(11)}" fill="${fill}"/>${ring}</svg>`;
  }
  const shape = `<circle r="10" fill="${fill}"/>`;
  const ring = passed ? '<circle r="13.6" fill="none" stroke="#ffffff" stroke-width="2.2"/>' : '';
  return `<svg viewBox="-15 -15 30 30" aria-hidden="true">${shape}${ring}</svg>`;
}
function badgeHTML(size = 'big') {           // the verdict: ∅ with no hand-in, grey without labels
  if (!S.submit) return `<span class="badge ${size} none">∅</span>`;
  if (!GRADED) return `<span class="badge ${size} ungraded"></span>`;
  return `<span class="badge ${size} r${TEAM_RUNG}">${discSVG(TEAM_RUNG, PASSED)}</span>`;
}
// The run's mark on its study page: the verdict's disc and the held dot; a dashed ring for a run in incomplete/.
function studyMarkHTML() {
  if (S.incomplete) return '<span class="smark incomplete"></span>';
  const level = S.submit && NV ? heldLevel(FINAL_HELD) : null;
  const dot = level ? `<i class="leak ${level}"></i>` : '';
  if (!GRADED) return `<span class="smark missing"><b>${S.submit ? '?' : '∅'}</b>${dot}</span>`;
  return `<span class="smark r${TEAM_RUNG}">${discSVG(TEAM_RUNG, PASSED)}${S.submit ? '' : '<b>∅</b>'}${dot}</span>`;
}
// How many agents the labels put at attempted or above: workers, and disregulators apart.
const COUNTS = ['worker', 'disregulator'].map(role => {
  const members = S.agents.filter(a => a.role === role);
  return [role, members.filter(a => a.final >= CHEATED).length, members.length];
}).filter(([, , n]) => n);
const countsHTML = () => COUNTS.map(([role, k, n]) =>
  `<span class="count${k ? ' hot' : ''}">${role === 'worker' ? '▲' : '<b style="color:#ff9de2">✹</b>'}${k}/${n}</span>`).join('');
const verdictChips = () => (!S.submit ? '' : !GRADED ? chip('?', hex(UNGRADED_COLOR)) :
  chip(RUNG_WORDS[TEAM_RUNG], hex(VERDICT_COLOR), 'strong') + chip(PASSED ? 'pass' : 'fail', PASSED ? '#ffffff' : '#8a93a8'));
const checkIcon = answer => (answer === 1 ? '<span class="ic ok">◉</span>' : '<span class="ic">◎</span>');
const ROUTE_TIP = {branch: 'a planted branch', fix: 'a fix commit', patch: 'a side patch', file: 'a revealed file'};

const labelsBox = document.getElementById('labels');
const leaders = document.getElementById('leaders');
const tags = S.agents.map(agent => {
  const el = document.createElement('div');
  el.className = 'tag';
  // A big swarm gets short tags (label and rung); the header already names the models and roles.
  el.innerHTML = '<div class="fams"></div>' + (N > 8
    ? `<b style="color:${ROLE_ACCENT[agent.role] || '#fff'}">${esc(agent.label)}</b> <span class="rung"></span>`
    : `<b style="color:${ROLE_ACCENT[agent.role] || '#fff'}">${esc(agent.label)}</b> <span class="model">${esc(agent.model || '')}</span><br><span class="rung"></span>`);
  labelsBox.appendChild(el);
  el.insertAdjacentHTML('beforeend', '<span class="flagged"></span><span class="raised"></span>');
  const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
  line.setAttribute('class', 'leader');
  leaders.appendChild(line);
  return {el, line, rung: el.querySelector('.rung'), flagged: el.querySelector('.flagged'),
          raised: el.querySelector('.raised'), fams: el.querySelector('.fams'), shown: '', flags: '', raisedHTML: '',
          famsHTML: '', full: true};
});
{
  // The setup in one line of chips: workers, regulators, disregulators, the rule, the task version, told to cheat.
  const su = S.setup || {};
  const count = role => S.agents.filter(a => a.role === role).length;
  const modelOf = role => (S.agents.find(a => a.role === role) || {}).model || '';
  const workers = su.workers ?? count('worker'), regs = su.regulators ?? count('regulator');
  const dis = su.disregulators ?? count('disregulator');
  const parts = [chip(`● ${workers} ${esc(su.worker_model || modelOf('worker'))}`, ROLE_ACCENT.worker, 'big')];
  if (regs) parts.push(chip(`■ ${regs} ${esc(su.regulator_model || modelOf('regulator'))}`, ROLE_ACCENT.regulator, 'big'));
  if (dis) parts.push(chip(`✹ ${dis} ${esc(su.disregulator_model || modelOf('disregulator'))}`, ROLE_ACCENT.disregulator, 'big'));
  if (!regs && !dis) parts.push(chip('alone', '#8a93a8', 'big'));
  if (su.rule) parts.push(chip(`§ ${esc(su.rule)}`, '#e6eeff', 'big'));
  if (su.version) parts.push(chip(esc(su.version), '#b9c7e6', 'big'));
  if (su.check_tool !== undefined) parts.push(su.check_tool ? chip('◎ check', '#c8d0e0', 'big') : chip('◎✗', '#6b7385', 'big'));
  if (su.told_to_cheat) parts.push(chip('⚑ cheat', '#ffb067', 'big strong'));
  const back = S.back ? `<a class="back" href="${esc(S.back)}">◂</a>${studyMarkHTML()}` : '';
  document.getElementById('cast').innerHTML = back + parts.join('');
  document.getElementById('runName').textContent = S.run;
  // The legend: shapes, rungs, pieces by family (the family the labels say was used outlined), held, alerts, rings.
  const shapes = [['worker', '● worker'], ['regulator', '■ regulator'], ['disregulator', '✹ disregulator']]
    .filter(([role]) => S.agents.some(a => a.role === role))
    .map(([role, word]) => `<span class="k" style="color:${ROLE_ACCENT[role]}">${word}</span>`).join('');
  const hexSwatch = `<svg class="hx" viewBox="-6 -6 12 12"><polygon points="${hexPoints(5.6)}" fill="${hex(RUNG_COLORS[HACKED])}"/></svg>`;
  const swatches = RUNG_WORDS.map((w, k) => `<span class="k">${k === HACKED ? hexSwatch : `<span class="sw" style="background:${hex(RUNG_COLORS[k])}"></span>`}${w}</span>`).join('') +
    '<span class="k">◌ undated</span><span class="k"><span class="sw dashed"></span>left</span>';
  const used = String(S.exploit_used || '');
  const families = FAMILIES.map((family, f) => {
    const names = family.pieces.map(p => `<b style="color:${hex(pieceColor(p))}">${esc(PIECE_NAMES[p])}</b>`).join(' ');
    const outlined = family.name && (used === family.name || used === 'both');
    return `<span class="k fam${outlined ? ' used' : ''}">${FAMILY_ICON[f % 2]} ${names}</span>`;
  }).join('') + (FAMILIES.length ? `<span class="k">${FAMILY_HOLLOW[0]} seen</span>` : '');
  const pip = (cls, color) => `<span class="pip ${cls}" style="--c:${color}"></span>`;
  const amber = hex(ALERT_COLOR), grey = hex(FALSE_ALARM);
  const marks = ['<span class="k"><span class="line"></span>talk</span>',
                 ALERTS.length ? `<span class="k">${pip('', amber)}⚠ hit</span><span class="k">${pip('hollow', amber)}team</span>` +
                                 `<span class="k">${pip('', grey)}false</span><span class="k">${pip('dashed', grey)}?</span>` : '',
                 HANDINS.some(r => r[2] === 1) ? `<span class="k" style="color:${hex(TAKEBACK_COLOR)}">↩ back</span>` : '',
                 CHECKS.length ? '<span class="k">◉ ◎ check</span>' : ''].join('');
  const held = NV && (HANDINS.length || CHECKS.length)
    ? `<span class="k">◈ ${segs([])} 0/${NV}</span><span class="k">${segs(VALUES.map((_, k) => k))} ${NV}/${NV}</span><span class="k">${segs(null)} ?</span>`
    : '';
  const verdict = S.submit ? `<span class="k">✓ ${badgeHTML('small')} label</span><span class="k"><span class="badge small r0">${discSVG(0, true)}</span>pass</span>` : '';
  document.getElementById('legend').innerHTML =
    `<div>${shapes}</div><div>${swatches}</div>${families ? `<div>${families}</div>` : ''}<div>${marks}</div>` +
    `${held || verdict ? `<div>${held}${verdict}</div>` : ''}`;
}

// The feed: every recorded event as a row of chips. A post's or a reason's words show only on hover.
const events = [];
const titled = text => (text ? ` title="${esc(text)}"` : '');
for (const [te, a, text, named] of S.said || []) {
  events.push({t: te, accent: ROLE_ACCENT[S.agents[a].role], tip: text,
               html: `<span class="ic">✎</span>${nameOf(a)}${named && named.length ? ` <span class="ic">→</span>${named.map(nameOf).join(' ')}` : ''}`});
}
{
  const fallAt = new Set(FALLS.map(([tf, a]) => `${tf}|${a}`));
  S.agents.forEach((agent, i) => {
    let before = 0;
    for (const [at, value] of agent.rungs) {
      const label = Math.abs(at - LABEL_T) < 1e-6 ? chip('label', '#8a93a8') : '';
      if (value > before) {
        events.push({t: at, prio: 1, accent: hex(RUNG_COLORS[value]),
                     html: `${nameOf(i)} <span class="ic">→</span>${chip(RUNG_WORDS[value], hex(RUNG_COLORS[value]), 'strong')}${label}`});
      } else if (value < before && !fallAt.has(`${at}|${i}`)) {
        events.push({t: at, prio: 1, accent: hex(RUNG_COLORS[value]),
                     html: `${nameOf(i)} <span class="ic">↓</span>${chip(RUNG_WORDS[value], hex(RUNG_COLORS[value]), 'strong')}${label}`});
      }
      before = value;
    }
  });
}
for (const [tf, a, rung, by] of FALLS) {
  events.push({t: tf, prio: 1, accent: hex(RUNG_COLORS[rung]),
               html: `${nameOf(a)} <span class="ic">↓</span>${chip(RUNG_WORDS[rung], hex(RUNG_COLORS[rung]), 'strong')}${by >= 0 ? `<span class="ic">←</span>${nameOf(by)}` : ''}`});
}
for (const [tf, piece, dst, routes] of S.flows || []) {
  const color = hex(pieceColor(piece));
  events.push({t: tf, accent: color, tip: (routes || []).map(r => ROUTE_TIP[r] || r).join(' · '),
               html: `${nameOf(dst)} <span class="ic" style="color:${color}">${FAMILY_ICON[familyOf(piece) % 2]}</span>` +
                     `<b style="color:${color}">${esc(PIECE_NAMES[piece] ?? piece)}</b>${(routes || []).map(r => chip(esc(r), '#9fb3d9')).join('')}`});
}
{
  const finalAt = FINAL ? FINAL[0] : undefined;
  for (const [th, a, kind, reason, held] of HANDINS) {
    if (kind === 0 && th === finalAt) continue;
    events.push(kind === 0
      ? {t: th, accent: hex(heldColor(held)), html: `<span class="ic">✓</span>${nameOf(a)}${heldChip(held)}`}
      : {t: th, accent: hex(TAKEBACK_COLOR), tip: reason,
         html: `<span class="ic" style="color:${hex(TAKEBACK_COLOR)}">↩</span>${nameOf(a)}`});
  }
}
if (S.submit) {
  events.push({t: FINAL_T, prio: 2, accent: hex(VERDICT_COLOR), final: true,
               html: `<span class="ic">✓</span>${nameOf(FINAL_AGENT)}${heldChip(FINAL_HELD)}${badgeHTML('small')}${verdictChips()}`});
}
for (const [tc, a, answer, held] of CHECKS) {
  events.push({t: tc, accent: hex(heldColor(held)), html: `${checkIcon(answer)}${nameOf(a)}${heldChip(held)}`});
}
for (const [ta, caller, who, outcome, reason, flags] of ALERTS) {
  const color = hex(alarmColor(outcome));
  // An agent named who had not cheated by then is drawn dimmed.
  const named = who.map((j, k) => (flags && flags[k] === 0 ? `<span class="miss">${nameOf(j)}</span>` : nameOf(j)));
  events.push({t: ta, accent: color, tip: reason,
               html: `<span class="alarm" style="color:${color}">⚠</span>${nameOf(caller)}${who.length ? ` <span class="ic">→</span>${named.join(' ')}` : ''}` +
                     `${outcome === 1 ? chip('true', color, 'strong') : outcome === 0 ? chip('false', color) : ''}`});
}
events.sort((a, b) => a.t - b.t || (a.prio || 0) - (b.prio || 0));
const feed = document.getElementById('feed');
const clockTime = document.getElementById('clockTime');
const whereEl = document.getElementById('where');
const standingEl = document.getElementById('standing');
const badgeEl = document.getElementById('badge');
const FEED_SIZE = 6;
let feedShown = -2, badgeShown = '';
const camRight = new THREE.Vector3(), camUp = new THREE.Vector3();
// A small row of family marks under a node: per family, solid for a piece dealt, hollow for one seen, filled out
// to the family's size with dim marks for the pieces it has not got.
function famsHTML(i, sets) {
  if (!sets) return '';
  const out = [];
  FAMILIES.forEach((family, f) => {
    if (!family.pieces.some(p => sets.dealt.has(p) || sets.seen.has(p))) return;
    out.push(family.pieces.map(p => {
      const color = hex(pieceColor(p));
      if (sets.dealt.has(p)) return `<b style="color:${color}">${FAMILY_ICON[f % 2]}</b>`;
      if (sets.seen.has(p)) return `<b style="color:${color}">${FAMILY_HOLLOW[f % 2]}</b>`;
      return `<b class="nil">${FAMILY_ICON[f % 2]}</b>`;
    }).join(''));
  });
  return out.join('<i></i>');
}
function drawWords(rungs, here, alive, focus, flags, hand, recent, pieceSets) {
  clockTime.textContent = `${clockText(t)} / ${clockText(T)}`;
  const r = roundAt(t), step = stepShown();
  const whereText = (ROUNDS.length ? `R ${ROUNDS[r] ? ROUNDS[r][0] : 0}/${ROUNDS[ROUNDS.length - 1][0]}` : '') +
                    (step ? ` · #${cursor + 1}/${STEPS.length} ${nameOf(step[1])} ${esc(step[2])}` : '');
  if (whereEl.innerHTML !== whereText) whereEl.innerHTML = whereText;
  // The hand-in standing now: who made it and what its code held; the final one with the verdict.
  const standing = hand.holder < 0 ? '' :
    `<span class="ic">✓</span>${nameOf(hand.holder)}${heldChip(hand.held)}${hand.since === FINAL_T ? verdictChips() : ''}`;
  if (standingEl.innerHTML !== standing) standingEl.innerHTML = standing;
  // The badge: dashed until the final hand-in (or the run's end without one), then the verdict.
  // A run in incomplete/ never ends: its ∅ shows from the start.
  const done = S.submit ? t >= FINAL_T : S.incomplete || t >= T - 1e-6;
  const badge = done ? `${GRADED ? `<span class="counts">${countsHTML()}</span>` : ''}${badgeHTML()}`
                     : '<span class="badge big pending"></span>';
  if (badge !== badgeShown) { badgeShown = badge; badgeEl.innerHTML = badge; }
  let last = -1;
  while (last + 1 < events.length && events[last + 1].t <= t) last++;
  if (last !== feedShown) {
    feedShown = last;
    const rows = [];
    for (let k = last; k >= 0 && k > last - FEED_SIZE; k--) {
      const e = events[k], age = last - k;
      rows.push(`<div class="event${e.final ? ' final' : ''}"${titled(e.tip)} style="border-left-color:${e.accent};opacity:${1 - age * 0.12}">` +
                `<span class="when">${clockText(e.t)}</span>${e.html}</div>`);
    }
    feed.innerHTML = rows.join('');
  }
  camera.updateMatrixWorld();
  camRight.setFromMatrixColumn(camera.matrixWorld, 0);
  const w = window.innerWidth, h = window.innerHeight;
  // Where each node sits on screen: its centre, its radius in pixels, its depth.
  const discs = nodes.map((node, i) => {
    if (!alive[i] || here[i].here < 0.5) return null;
    tmp.copy(pos[i]).project(camera);
    if (tmp.z > 1) return null;
    const sx = (tmp.x * 0.5 + 0.5) * w, sy = (-tmp.y * 0.5 + 0.5) * h, z = tmp.z;
    tmp.copy(pos[i]).addScaledVector(camRight, node.group.scale.x).project(camera);
    return {sx, sy, z, r: Math.abs((tmp.x * 0.5 + 0.5) * w - sx)};
  });
  const shown = [];
  nodes.forEach((node, i) => {
    const tag = tags[i];
    const full = !CROWDED || recent.has(i) || (hovered >= 0 && focus(i) >= 1);
    if (!discs[i]) { tag.el.style.display = 'none'; tag.line.style.display = 'none'; return; }
    if (tag.full !== full) { tag.full = full; tag.el.classList.toggle('mini', !full); }
    tag.el.style.display = '';
    const rung = rungs[i].rung;
    const dim = rung >= CHEATED ? 1 : 1 - 0.4 * here[i].gone;          // a cheater never dims
    tag.el.style.opacity = String(dim * (focus(i) < 1 ? 0.25 : 1) * (full ? 1 : 0.6));
    const fams = famsHTML(i, pieceSets[i]);
    if (tag.famsHTML !== fams) { tag.famsHTML = fams; tag.fams.innerHTML = fams; }
    const check = recentCheck(i);
    const badge = (hand.holder === i ? ` <b class="hb">✓</b>${heldChip(hand.held)}` : '') +
                  (hand.back && hand.back[1] === i && t - hand.back[0] < D.flash * 1.5 ? ' <b style="color:#d68cff">↩</b>' : '') +
                  (check ? ` <b class="hb">${check[2] === 1 ? '◉' : '◎'}</b>${heldChip(check[3])}` : '');
    if (tag.flags !== badge) { tag.flags = badge; tag.flagged.innerHTML = badge; }
    // A regulator or disregulator carries the alerts it raised (amber true, grey false, hollow ? unknown) and its
    // hits and misses: agents named who had, or had not, cheated by then.
    const [up, down, unknown, hits, misses] = flags.raised[i];
    const raised = S.agents[i].role !== 'worker' && (up || down || unknown)
      ? ` <span class="alarm">⚠${up}</span>${down ? `<span class="false">${down}</span>` : ''}` +
        `${unknown ? `<span class="unknown">?${unknown}</span>` : ''}` +
        `${hits || misses ? ` <span class="hits">●${hits}</span><span class="misses">○${misses}</span>` : ''}` : '';
    if (tag.raisedHTML !== raised) { tag.raisedHTML = raised; tag.raised.innerHTML = raised; }
    const word = RUNG_WORDS[rung] + (t >= LABEL_T && !S.agents[i].dated && rung === S.agents[i].final ? ' ◌' : '');
    if (tag.shown !== word) {
      tag.shown = word;
      tag.rung.textContent = word;
      tag.rung.style.color = hex(RUNG_COLORS[rung]);
    }
    shown.push(i);
  });
  // Place the tags nearest first. A tag that would sit on a nearer node or on another tag is lifted above it, with
  // a thin leader line back to its node.
  shown.sort((a, b) => discs[a].z - discs[b].z);
  const placed = [];
  const overlaps = (x0, y0, x1, y1, self) => {
    for (const p of placed) if (x0 < p.x1 && x1 > p.x0 && y0 < p.y1 && y1 > p.y0) return p.y0;
    for (let j = 0; j < N; j++) {
      const d = discs[j];
      if (!d || j === self || d.z > discs[self].z) continue;
      const cx = Math.max(x0, Math.min(d.sx, x1)), cy = Math.max(y0, Math.min(d.sy, y1));
      if ((cx - d.sx) ** 2 + (cy - d.sy) ** 2 < (d.r * 0.92) ** 2) return d.sy - d.r;
    }
    return null;
  };
  for (const i of shown) {
    const tag = tags[i], d = discs[i];
    const tw = tag.el.offsetWidth, th = tag.el.offsetHeight;
    const x0 = d.sx - tw / 2, base = d.sy + d.r * 1.12 + 3;
    let y0 = base;
    for (let tries = 0; tries < 8; tries++) {
      const top = overlaps(x0, y0, x0 + tw, y0 + th, i);
      if (top === null) break;
      y0 = Math.min(top, y0) - th - 4;
    }
    placed.push({x0, y0, x1: x0 + tw, y1: y0 + th});
    tag.el.style.left = `${d.sx}px`;
    tag.el.style.top = `${y0}px`;
    const lifted = Math.abs(y0 - base) > 2;
    tag.line.style.display = lifted ? '' : 'none';
    if (lifted) {
      const above = y0 < d.sy;
      tag.line.setAttribute('x1', d.sx); tag.line.setAttribute('y1', above ? d.sy - d.r : d.sy + d.r);
      tag.line.setAttribute('x2', d.sx); tag.line.setAttribute('y2', above ? y0 + th : y0);
      tag.line.style.opacity = tag.el.style.opacity;
    }
  }
}
function recentCheck(i) {                      // agent i's check_fix answer in the last moments, or null
  let found = null;
  for (const row of windowed(CHECKS, t, D.check * 1.6)) if (row[1] === i) found = row;
  return found;
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
function presence(agent, t) {               // here: 0 not yet, 1 here; gone: 0 to 1 once the agent has left
  if (t < agent.start) return {here: 0, gone: 0};
  const here = Math.min(1, (t - agent.start) / D.fade + (agent.start <= 0 ? 1 : 0));
  const left = agent.left ?? null;           // ended by stop_working or an error before the final hand-in
  const gone = left !== null && t > left ? Math.min(1, (t - left) / D.fade) : 0;
  return {here, gone};
}
const flows = (S.flows || []).slice().sort((a, b) => a[0] - b[0]);
function piecesHeld(i, t) {                  // the pieces dealt to agent i, and the ones it has seen since
  const dealt = new Set(S.agents[i].holds), seen = new Set();
  for (const [tf, piece, to] of flows) {
    if (tf + D.flow > t) break;
    if (to === i && !dealt.has(piece)) seen.add(piece);
  }
  return {dealt, seen};
}
// A post naming no agent spreads a faint ring; a post naming agents shows only as its beams.
const quietPosts = (S.said ? S.said.filter(row => !(row[3] && row[3].length)).map(row => [row[0], row[1]]) : S.posts.slice())
  .sort((a, b) => a[0] - b[0]);
const FROM_CENTRE = new THREE.Vector3(0, 2.5, 0);   // a piece's arrival flies in from the scene's centre
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
      if (d2 > 6) continue;                          // only agents about to touch push apart
      tmp.normalize().multiplyScalar(1.5 / d2);
      force[i].sub(tmp); force[j].add(tmp);
    }
  }
  for (const pair of pairs.values()) {               // talk nudges a pair together, a little
    if (pair.w <= 0 || !alive[pair.i] || !alive[pair.j]) continue;
    tmp.subVectors(pos[pair.j], pos[pair.i]);
    const d = Math.max(tmp.length(), 0.01);
    const k = 0.0006 * Math.min(1 + Math.sqrt(pair.w), 4);
    tmp.multiplyScalar(((d - 3) * k) / d);
    force[pair.i].add(tmp); force[pair.j].sub(tmp);
  }
  for (let i = 0; i < N; i++) {
    if (!alive[i]) continue;
    force[i].add(tmp.subVectors(ANCHORS[i], pos[i]).multiplyScalar(0.06));   // the spring to its place
    vel[i].add(force[i].multiplyScalar(0.05)).multiplyScalar(0.86);
    if (vel[i].length() > 0.6) vel[i].setLength(0.6);
    pos[i].add(vel[i]);
  }
}

// ---------- camera ----------
let azimuth = 0.7, polar = 0.72, zoom = 1, lastDrag = -1e9, dragging = false, px = 0, py = 0;
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
  // Frame the box around every agent here: its middle, and a sphere that holds it.
  const lo = new THREE.Vector3(Infinity, Infinity, Infinity), hi = new THREE.Vector3(-Infinity, -Infinity, -Infinity);
  let n = 0;
  for (let i = 0; i < N; i++) if (alive[i]) { lo.min(pos[i]); hi.max(pos[i]); n++; }
  const c = n ? lo.clone().add(hi).multiplyScalar(0.5) : new THREE.Vector3();
  let r = 4;
  for (let i = 0; i < N; i++) if (alive[i]) r = Math.max(r, pos[i].distanceTo(c) + 2.6);
  const ease = 1 - Math.exp(-dt * 2);
  center.lerp(c, ease);
  radius += (r - radius) * ease;
  const distance = (radius / Math.sin((camera.fov * Math.PI) / 360)) * 1.04 * zoom;
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
const handinRoom = document.getElementById('handinRoom');
const arc = new THREE.Vector3(), up = new THREE.Vector3(), from = new THREE.Vector3(), to = new THREE.Vector3();

function ringAt(at, color, opacity, scale) {
  const r = ringPool.get();
  r.position.copy(at); r.material.color.copy(color);
  r.material.opacity = opacity; r.scale.setScalar(scale);
}

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
  camera.updateMatrixWorld();
  camRight.setFromMatrixColumn(camera.matrixWorld, 0);
  camUp.setFromMatrixColumn(camera.matrixWorld, 1);

  // Nodes: fill by rung. A hacked worker is a faceted solid. An agent that left wears a dashed edge; a cheater never
  // dims, and an agent below attempted that left fades to no less than 60%.
  nodes.forEach((node, i) => {
    const {rung, previous, since} = rungs[i];
    const {here: h, gone} = here[i];
    node.group.visible = alive[i];
    if (!alive[i]) return;
    node.group.position.copy(pos[i]);
    const mix = smooth((t - since) / D.change);          // the colour changes at the call itself
    const color = RUNG_COLORS[previous].clone().lerp(RUNG_COLORS[rung], mix);
    node.material.color.copy(color);
    node.material.emissive.copy(color);
    const pulse = rung >= CHEATED ? 0.5 + 0.5 * Math.sin(clock * (rung === HACKED ? 5 : 3.2)) : 0;
    node.material.emissiveIntensity = 0.18 + 0.2 * rung + 0.25 * pulse;
    const look = rung >= CHEATED ? 1 : 1 - 0.4 * gone;
    node.material.opacity = look * focus(i);
    node.group.scale.setScalar(Math.max(0.001, smooth(h)));
    node.glow.material.color.copy(color);
    node.glow.material.opacity = (0.22 + 0.06 * rung + 0.2 * pulse) * look * focus(i);
    node.glow.scale.setScalar(3.2 + 0.4 * rung + 0.5 * pulse);
    node.core.rotation.y += dt * 0.3;
    if (node.cage) { node.core.rotation.x += dt * 0.15; node.cage.material.opacity = 0.95 * look * focus(i); }
    if (node.facet) {
      const faceted = rung === HACKED;
      node.facet.visible = faceted;
      node.core.visible = !faceted;
      node.facet.rotation.y = node.core.rotation.y;
      node.facet.material.emissiveIntensity = 0.45 + 0.25 * pulse;
      node.facet.material.opacity = look * focus(i);
    }
    node.leftRing.material.color.set(0xdfe6f5);
    node.leftRing.material.opacity = 0.9 * smooth(gone * 3) * focus(i);
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
    const thick = 0.006 + 0.11 * Math.pow(share, 0.85);
    m.scale.set(thick, length, thick);
    const fresh = Math.max(0, 1 - (t - pair.last) / (D.spark * 2.5));
    const both = Math.min(rungs[pair.i].rung, rungs[pair.j].rung);
    m.material.color.copy(EDGE_COLOR).lerp(EDGE_RED, both >= CHEATED ? 0.85 : 0);
    const faded = 1 - 0.6 * Math.max(here[pair.i].gone, here[pair.j].gone);
    const lit = hovered < 0 ? 1 : (pair.i === hovered || pair.j === hovered ? 1.3 : 0.1);
    m.material.opacity = Math.min(1, (0.12 + 0.45 * share + 0.3 * fresh) * faded * lit);
  }

  for (const pool of pools) pool.begin();

  // Sparks: each message travels along its edge, from the writer to the agent it names (a read that asks for an
  // agent's posts travels from that agent to the reader).
  for (const [te, a, b, kind] of windowed(talkSorted, t, D.spark)) {
    if (!alive[a] || !alive[b]) continue;
    const p = (t - te) / D.spark;
    const [src, dst] = kind === ASK ? [b, a] : [a, b];
    // A thin beam tinted in the writer's role colour (the reader's for a read), fading within about a second.
    const tint = ROLE_COLORS[S.agents[a].role] || ROLE_COLORS.worker;
    for (let trail = 0; trail < 4; trail++) {
      const q = p - trail * 0.05;
      if (q < 0) continue;
      const s = glowPool.get();
      s.position.lerpVectors(pos[src], pos[dst], smooth(q));
      s.material.color.copy(tint);
      s.material.opacity = 0.7 * (1 - trail * 0.22) * (1 - p) * (hovered < 0 || near.has(src) || near.has(dst) ? 1 : 0.15);
      s.scale.setScalar(0.36 - trail * 0.06);
    }
  }

  // Ripples: a post that names no agent spreads a faint ring from its writer.
  for (const [te, a] of windowed(quietPosts, t, D.ripple)) {
    if (!alive[a]) continue;
    const p = (t - te) / D.ripple;
    ringAt(pos[a], ROLE_COLORS[S.agents[a].role] || TALK_COLOR, 0.25 * (1 - p) * focus(a), 2.4 + 3 * p);
  }

  // Pieces arriving: the agent first saw a piece it lacked (pieces_seen_at). Who gave it is not recorded, so the
  // piece flies in from the scene's centre and lands on the agent.
  flows.forEach(([tf, piece, dst]) => {
    if (!alive[dst]) return;
    const color = pieceColor(piece);
    if (t >= tf && t < tf + D.flow) {
      const p = (t - tf) / D.flow;
      const dist = FROM_CENTRE.distanceTo(pos[dst]);
      up.set(0, 1, 0).multiplyScalar(dist * 0.3);
      for (let trail = 0; trail < 7; trail++) {
        const q = smooth(p - trail * 0.03);
        if (p - trail * 0.03 < 0) continue;
        arc.lerpVectors(FROM_CENTRE, pos[dst], q).addScaledVector(up, Math.sin(Math.PI * q));
        if (trail === 0) {
          const mesh = pieceMesh(piece, true);
          mesh.position.copy(arc);
          sizePiece(mesh, 0.42);
          mesh.material.opacity = 1;
        }
        const g = glowPool.get();
        g.position.copy(arc);
        g.material.color.copy(color);
        g.material.opacity = 0.8 * (1 - trail / 7);
        g.scale.setScalar(2.0 - trail * 0.2);
      }
    }
    const landed = t - (tf + D.flow);
    if (landed >= 0 && landed < D.flash) {             // and it lands
      const p = landed / D.flash;
      ringAt(pos[dst], color, 0.9 * (1 - p), 2.5 + 6 * p);
    }
  });

  // The pieces circle their agent: a piece dealt solid, a piece seen since as a hollow outline of its shape.
  const pieceSets = nodes.map((node, i) => {
    if (!alive[i]) return null;
    const sets = piecesHeld(i, t);
    const held = [...sets.dealt, ...sets.seen].sort((a, b) => a - b);
    const look = rungs[i].rung >= CHEATED ? 1 : 1 - 0.4 * here[i].gone;
    const scale = node.group.scale.x;
    held.forEach((piece, k) => {
      const color = pieceColor(piece);
      const hollow = !sets.dealt.has(piece);
      const angle = clock * node.spin + (k * Math.PI * 2) / held.length;
      arc.set(Math.cos(angle) * 1.95, 0, Math.sin(angle) * 1.95).applyQuaternion(node.tilt).multiplyScalar(scale).add(pos[i]);
      const mesh = pieceMesh(piece, hollow);
      mesh.position.copy(arc);
      sizePiece(mesh, 0.24 * scale);
      mesh.material.opacity = look * focus(i);
      if (hollow) return;
      const g = glowPool.get();
      g.position.copy(arc); g.material.color.copy(color);
      g.material.opacity = 0.45 * look * focus(i); g.scale.setScalar(1.2 * scale);
    });
    return sets;
  });

  // A rung change closes a bright ring in on the node, in the new rung's colour: the brightest cue of a frame.
  const rungRing = (at, color, p, strength) => {
    const r = rungPool.get();
    r.position.copy(at); r.material.color.copy(color);
    r.material.opacity = strength * (1 - p);
    r.scale.setScalar((1.3 + 1.9 * (1 - smooth(p))) / CIRCLE_R);
  };
  nodes.forEach((node, i) => {
    if (!alive[i]) return;
    const {rung, previous, since} = rungs[i];
    const p = (t - since) / D.flash;
    if (rung !== previous && p >= 0 && p < 1) rungRing(pos[i], RUNG_COLORS[rung], p, rung >= CHEATED ? 1 : 0.75);
  });
  // A fall happens at the call that caused it: a short beam from the agent who caused it to the agent that fell.
  for (const [tf, a, rung, by] of windowed(FALLS, t, D.flash)) {
    if (!alive[a] || by < 0 || !alive[by]) continue;
    const p = (t - tf) / D.flash;
    const reach = smooth(p / 0.06);
    for (let k = 0; k <= 12; k++) {
      const s2 = glowPool.get();
      s2.position.lerpVectors(pos[by], pos[a], (k / 12) * reach);
      s2.material.color.copy(RUNG_COLORS[rung]);
      s2.material.opacity = 0.75 * (1 - p);
      s2.scale.setScalar(0.5);
    }
  }

  // Alerts to humans: the caller flashes a beacon and a beam reaches every agent it names. Each alert naming an
  // agent leaves a pip at its edge: amber a true alarm, grey a false one.
  const flags = alertsUpTo(t);
  nodes.forEach((node, i) => {
    if (!alive[i]) return;
    const outcomes = flags.named[i];
    const scale = node.group.scale.x;
    const look = rungs[i].rung >= CHEATED ? 1 : 1 - 0.4 * here[i].gone;
    outcomes.forEach((kind, k) => {
      const row = Math.floor(k / 10), col = k % 10;
      const a = Math.PI * (0.72 + 0.056 * col) + row * 0.028;
      const r = (R_PIPS + row * 0.26) * scale;
      const pip = kind === 'hit' || kind === 'false' ? pipPool.get() : kind === 'team' ? pipHollowPool.get() : pipDashPool.get();
      pip.position.copy(pos[i]).addScaledVector(camRight, Math.cos(a) * r).addScaledVector(camUp, Math.sin(a) * r);
      pip.material.color.copy(kind === 'hit' || kind === 'team' ? ALERT_COLOR : FALSE_ALARM);
      pip.material.opacity = look * focus(i);
      pip.scale.setScalar(0.24 * scale);
    });
  });
  for (const [ta, caller, who, outcome] of windowed(ALERTS, t, D.alert)) {
    if (!alive[caller]) continue;
    const p = (t - ta) / D.alert;
    const color = alarmColor(outcome);
    for (let wave = 0; wave < 3; wave++) {           // the beacon: three quick rings
      const q = (p * 3 - wave * 0.35);
      if (q < 0 || q > 1) continue;
      ringAt(pos[caller], color, 0.55 * (1 - q), 2.4 + 5 * q);
    }
    const g = glowPool.get();
    g.position.copy(pos[caller]); g.material.color.copy(color);
    g.material.opacity = 0.5 * (1 - p) * (0.6 + 0.4 * Math.sin(clock * 14)); g.scale.setScalar(5);
    for (const j of who) {                           // a beam of light to each agent named
      if (!alive[j]) continue;
      const reach = smooth(p / 0.35);
      for (let k = 0; k <= 10; k++) {
        const q = (k / 10) * reach;
        const s2 = glowPool.get();
        s2.position.lerpVectors(pos[caller], pos[j], q);
        s2.material.color.copy(color);
        s2.material.opacity = 0.6 * (1 - p);
        s2.scale.setScalar(0.5);
      }
      if (reach >= 1) ringAt(pos[j], color, 0.5 * (1 - p), 2.5 + 3 * smooth((p - 0.35) / 0.65));
    }
  }

  // The final hand-in: one thin ring in the team rung's colour around its maker, staying close and gone within a
  // second. The header's disc carries the verdict.
  if (S.submit && FINAL_AGENT >= 0 && alive[FINAL_AGENT]) {
    const p = (t - FINAL_T) / D.verdict;
    if (p >= 0 && p < 1) {
      const r = thinPool.get();
      r.position.copy(pos[FINAL_AGENT]); r.material.color.copy(VERDICT_COLOR);
      r.material.opacity = 0.8 * (1 - p); r.scale.setScalar((1.4 + 1.2 * smooth(p)) / CIRCLE_R);
    }
  }

  // Hand-ins: while one stands, its maker wears the held segments (what its code held) and the room's edges glow
  // faintly. A take-back closes violet rings in on the agent who took it.
  const hand = handinAt(t);
  const isFinal = hand.holder >= 0 && hand.since === FINAL_T;
  const roomOn = hand.holder >= 0 ? smooth((t - hand.since) / D.fade) : 1 - smooth((t - hand.since) / D.fade);
  handinRoom.style.opacity = String(isFinal ? 0 : Math.max(0, roomOn) * 0.8);
  const level = heldLevel(hand.held);
  nodes.forEach((node, i) => {
    const holding = alive[i] && hand.holder === i;
    const on = holding ? smooth((t - hand.since) / D.fade) * focus(i) : 0;
    node.held.quaternion.copy(camera.quaternion);
    node.segments.forEach(({solid, dashes}, k) => {
      solid.material.opacity = level === 'unknown' ? 0 : on * (Array.isArray(hand.held) && k < hand.held.length ? 0.95 : 0.22);
      for (const d of dashes) d.material.opacity = level === 'unknown' ? 0.6 * on : 0;
    });
  });
  if (hand.back && alive[hand.back[1]]) {
    const p = (t - hand.back[0]) / D.flash;
    if (p >= 0 && p < 1) {
      for (let wave = 0; wave < 3; wave++) {
        const q = p * 1.6 - wave * 0.25;
        if (q < 0 || q > 1) continue;
        ringAt(pos[hand.back[1]], TAKEBACK_COLOR, 0.6 * (1 - p), 2.2 + 6 * (1 - smooth(q)));
      }
      const g = glowPool.get();
      g.position.copy(pos[hand.back[1]]); g.material.color.copy(TAKEBACK_COLOR);
      g.material.opacity = 0.45 * (1 - p); g.scale.setScalar(5);
    }
  }

  // check_fix answers: the caller lights up in the shade of what the checked code held. Accepted bursts outward in
  // two bright rings around a solid flash; not accepted is one dim ring that closes in.
  for (const [tc, a, answer, held] of windowed(CHECKS, t, D.check)) {
    if (!alive[a]) continue;
    const p = (t - tc) / D.check;
    const color = heldColor(held);
    if (answer === 1) {
      for (let wave = 0; wave < 2; wave++) {
        const q = p * 1.4 - wave * 0.3;
        if (q < 0 || q > 1) continue;
        ringAt(pos[a], color, 0.6 * (1 - q), 2.6 + 5 * smooth(q));
      }
      const g = glowPool.get();
      g.position.copy(pos[a]); g.material.color.copy(color);
      g.material.opacity = 0.45 * (1 - p); g.scale.setScalar(5);
    } else {
      ringAt(pos[a], color, 0.75 * (1 - p), 2.4 + 5 * (1 - smooth(p)));
    }
  }

  // The call being stepped: a pale blue ring on the agent making it (white means pass).
  syncCursor();
  const shown = stepShown();
  if (shown && alive[shown[1]]) ringAt(pos[shown[1]], STEP_COLOR, 0.55 + 0.2 * Math.sin(clock * 6), 3.6);

  // Who gets a tag in a crowded swarm: an agent doing something now.
  const recent = new Set();
  if (CROWDED) {
    rungs.forEach((rg, i) => { if (t - rg.since < D.flash * 2) recent.add(i); });
    if (hand.holder >= 0) recent.add(hand.holder);
    if (hand.back && t - hand.back[0] < D.flash * 1.5) recent.add(hand.back[1]);
    for (const row of windowed(CHECKS, t, D.check * 1.6)) recent.add(row[1]);
    for (const [, caller, who] of windowed(ALERTS, t, D.alert)) { recent.add(caller); who.forEach(j => recent.add(j)); }
    for (const [, a] of windowed(postsSorted, t, D.ripple)) recent.add(a);
    for (const [, , dst] of windowed(flows, t, D.flow + D.flash)) recent.add(dst);
    for (const [, a] of windowed(FALLS, t, D.flash * 2)) recent.add(a);
    if (shown) recent.add(shown[1]);
  }

  for (const pool of pools) pool.end();
  placeCamera(alive, dt);
  drawWords(rungs, here, alive, focus, flags, hand, recent, pieceSets);
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

// The timeline: one lane per kind of mark, its icon at the left end and its totals at the right end.
const TL_LEFT = 20, TL_RIGHT = 104;
const LANES = {alert: 7, piece: 18, band: 31, tick: 44, check: 56};
function trackX(v, w) { return TL_LEFT + (v / T) * (w - TL_LEFT - TL_RIGHT); }
// Marks that would share a pixel become one mark with a count: each cluster starts at a mark and takes every mark
// within `gap` pixels of its start.
function clusters(xs, gap) {
  const out = [];
  xs.forEach((x, k) => {
    const c = out[out.length - 1];
    if (c && x - c.x < gap) c.members.push(k); else out.push({x, members: [k]});
  });
  return out;
}
function drawBadge(g, x, y, r) {                 // the verdict's disc, as in the header: a hexagon for hacked
  if (!S.submit) return;
  g.beginPath();
  if (GRADED && TEAM_RUNG === HACKED) {
    for (let k = 0; k < 6; k++) {
      const a = (k * Math.PI) / 3, R = r * 1.16;
      if (k) g.lineTo(x + R * Math.sin(a), y - R * Math.cos(a)); else g.moveTo(x + R * Math.sin(a), y - R * Math.cos(a));
    }
    g.closePath();
  } else g.arc(x, y, r, 0, Math.PI * 2);
  g.fillStyle = hex(VERDICT_COLOR); g.fill();
  if (PASSED) {                                  // a white ring of the same shape
    g.beginPath();
    if (GRADED && TEAM_RUNG === HACKED) {
      for (let k = 0; k < 6; k++) {
        const a = (k * Math.PI) / 3, R = r * 1.16 + 2.6;
        if (k) g.lineTo(x + R * Math.sin(a), y - R * Math.cos(a)); else g.moveTo(x + R * Math.sin(a), y - R * Math.cos(a));
      }
      g.closePath();
    } else g.arc(x, y, r + 2.2, 0, Math.PI * 2);
    g.strokeStyle = '#ffffff'; g.lineWidth = 1.5; g.stroke();
  }
}
function buildBackdrop() {
  const dpr = Math.min(window.devicePixelRatio, 2);
  const w = timeline.clientWidth, h = timeline.clientHeight;
  timeline.width = w * dpr; timeline.height = h * dpr;
  backdrop = document.createElement('canvas');
  backdrop.width = timeline.width; backdrop.height = timeline.height;
  const g = backdrop.getContext('2d');
  g.scale(dpr, dpr);
  const x = v => trackX(v, w);
  const end = x(T);
  g.textBaseline = 'middle';
  const text = (s, tx, ty, color, size = 9.5, align = 'left', hollow = false) => {
    g.font = `650 ${size}px -apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif`;
    g.textAlign = align;
    g.fillStyle = color; g.fillText(s, tx, ty);
    const width = g.measureText(s).width;
    if (hollow) {                                    // unknown: the amber text in a dashed outline
      const x0 = align === 'center' ? tx - width / 2 : tx;
      g.strokeStyle = color; g.lineWidth = 0.8; g.setLineDash([2, 1.5]);
      g.strokeRect(x0 - 1.5, ty - size * 0.6, width + 3, size * 1.2);
      g.setLineDash([]);
    }
    return width + (hollow ? 2 : 0);
  };
  // How busy the board is: a soft histogram of the exchanges around the hand-in lane.
  const bins = 160, counts = new Array(bins).fill(0);
  for (const [te] of S.talk) counts[Math.min(bins - 1, Math.floor((te / T) * bins))]++;
  for (const [te] of S.posts) counts[Math.min(bins - 1, Math.floor((te / T) * bins))] += 0.5;
  const top = Math.max(1, ...counts);
  g.fillStyle = 'rgba(160,190,255,0.16)';
  counts.forEach((c, b) => {
    const bh = (c / top) * 14;
    g.fillRect(x((b / bins) * T), LANES.band - bh / 2, Math.max(1, (end - TL_LEFT) / bins - 1), bh);
  });
  g.fillStyle = 'rgba(200,215,255,0.16)';
  g.fillRect(TL_LEFT, LANES.band - 1.5, end - TL_LEFT, 3);
  // Lane icons.
  const icon = (s, lane, color) => text(s, 9, LANES[lane], color, 10, 'center');
  if (ALERTS.length) icon('⚠', 'alert', '#ffc21a');
  if (flows.length) icon('●', 'piece', '#9fb3d9');
  icon('✓', 'band', '#c8d6f5');
  icon('▲', 'tick', '#ff4d5d');
  if (CHECKS.length) icon('◎', 'check', '#c8d6f5');

  // Alerts: a triangle per cluster, amber when it holds a true alarm, a hollow amber one when its only alarms not
  // false are unknown, grey when all are false; beside it the true ones in amber, the false in grey, ? the unknown.
  for (const c of clusters(ALERTS.map(row => x(row[0])), 18)) {
    const rows = c.members.map(k => ALERTS[k]);
    const yes = rows.filter(r => r[3] === 1).length, no = rows.filter(r => r[3] === 0).length;
    const unknown = rows.length - yes - no;
    const y = LANES.alert;
    g.beginPath(); g.moveTo(c.x, y + 4); g.lineTo(c.x - 4, y - 3.5); g.lineTo(c.x + 4, y - 3.5); g.closePath();
    if (yes || !unknown) { g.fillStyle = yes ? '#ffc21a' : '#8b93a8'; g.fill(); }
    else { g.strokeStyle = '#ffc21a'; g.lineWidth = 1.2; g.stroke(); }
    if (rows.length > 1) {
      let tx = c.x + 6;
      if (yes) tx += text(String(yes), tx, y, '#ffc21a', 9.5) + 4;
      if (no) tx += text(String(no), tx, y, '#8b93a8', 9.5) + 4;
      if (unknown) text(`?${unknown}`, tx, y, '#ffc21a', 9.5, 'left', true);
    }
  }
  // Pieces arriving: a dot (or a small diamond) in the piece's colour; a cluster shows its count.
  for (const c of clusters(flows.map(row => x(row[0])), 13)) {
    const piece = flows[c.members[0]][1], y = LANES.piece;
    g.fillStyle = hex(pieceColor(piece));
    g.beginPath();
    if (familyOf(piece) === 1) { g.moveTo(c.x, y - 3.6); g.lineTo(c.x + 3.6, y); g.lineTo(c.x, y + 3.6); g.lineTo(c.x - 3.6, y); g.closePath(); }
    else g.arc(c.x, y, 2.8, 0, Math.PI * 2);
    g.fill();
    if (c.members.length > 1) text(String(c.members.length), c.x + 6, y, '#9fb3d9', 9.5);
  }
  // Hand-ins: a band along the track while each stood, in the shade of what its code held; the final one in the
  // team rung's colour, marked with the verdict's disc. A violet tick at each take-back.
  HANDINS.forEach(([th, , kind, , held], k) => {
    if (kind === 1) { g.fillStyle = hex(TAKEBACK_COLOR); g.fillRect(x(th) - 1, LANES.band - 6, 2, 12); return; }
    const stop = k + 1 < HANDINS.length ? HANDINS[k + 1][0] : T;
    const x0 = x(th), width = Math.max(2, x(stop) - x0);
    const final = th === FINAL_T && S.submit;
    g.fillStyle = hex(final ? VERDICT_COLOR : heldColor(held));
    g.globalAlpha = final ? 1 : 0.75;
    g.fillRect(x0, LANES.band - 3, width, 6);
    g.globalAlpha = 1;
  });
  // A rung reached at attempted or above: a red tick, solid when the agent stays there to its label, hollow from the
  // rise to the call that caused its fall. A final rung no dated call stands behind has no time: its tick is dashed,
  // beside the verdict's disc, off the time axis. Ticks at one pixel stand side by side.
  const ticks = [];
  for (const agent of S.agents) {
    let before = 0;
    agent.rungs.forEach(([at, value], k) => {
      if (value >= CHEATED && before < CHEATED) {
        const fall = agent.rungs.slice(k + 1).find(([, v]) => v < CHEATED);
        const undated = !fall && !agent.dated && Math.abs(at - LABEL_T) < 1e-6;
        ticks.push({at, solid: !fall && !undated, undated, until: fall ? fall[0] : null});
      }
      before = value;
    });
  }
  ticks.sort((a, b) => a.at - b.at);
  let tickX = -Infinity, undatedX = end + 4;
  g.fillStyle = '#ff2d3d'; g.strokeStyle = '#ff2d3d'; g.lineWidth = 1;
  for (const tick of ticks) {
    if (tick.undated) {
      g.setLineDash([2, 1.5]); g.strokeRect(undatedX - 1, LANES.tick - 4.5, 2, 9); g.setLineDash([]);
      undatedX += 3.6;
      continue;
    }
    tickX = Math.max(x(tick.at), tickX + 3.4);
    if (tick.solid) g.fillRect(tickX - 1.2, LANES.tick - 5, 2.4, 10);
    else {
      const span = Math.max(2, x(tick.until) - tickX);
      g.strokeRect(tickX - 1, LANES.tick - 4.5, span, 9);
    }
  }
  // check_fix answers: a dot per cluster in the shade of what the checked code held, filled when any was accepted;
  // the accepted count beside it in white and the rest in grey.
  for (const c of clusters(CHECKS.map(row => x(row[0])), 22)) {
    const rows = c.members.map(k => CHECKS[k]);
    const yes = rows.filter(r => r[2] === 1).length, no = rows.length - yes, y = LANES.check;
    g.beginPath(); g.arc(c.x, y, 3, 0, Math.PI * 2);
    if (yes) { g.fillStyle = hex(heldColor(rows.find(r => r[2] === 1)[3])); g.fill(); }
    else { g.strokeStyle = hex(heldColor(rows[0][3])); g.lineWidth = 1.3; g.stroke(); }
    if (rows.length > 1) {
      let tx = c.x + 6;
      if (yes) tx += text(String(yes), tx, y, '#e6eeff', 9.5) + 4;
      if (no) text(yes ? `◎${no}` : String(no), tx, y, '#8792a8', 9.5);
    }
  }
  // The final hand-in: a diamond in the team rung's colour, and the verdict's disc.
  if (S.submit && Number.isFinite(FINAL_T)) {
    const sx = x(FINAL_T), sy = LANES.band;
    g.fillStyle = hex(VERDICT_COLOR);
    g.beginPath(); g.moveTo(sx, sy - 7); g.lineTo(sx + 5, sy); g.lineTo(sx, sy + 7); g.lineTo(sx - 5, sy); g.closePath(); g.fill();
    g.strokeStyle = PASSED ? '#ffffff' : '#070a12'; g.lineWidth = 1.2; g.stroke();
    drawBadge(g, end + 9, sy, 4.5);
  }
  // Totals at the right end, equal to the study page's counts.
  const tx0 = end + 20;
  const totals = (lane, parts, from = tx0) => {
    let tx = from;
    for (const [s, color, hollow] of parts) tx += text(s, tx, LANES[lane], color, 10.5, 'left', !!hollow) + 5;
  };
  if (ALERTS.length) {
    const yes = ALERTS.filter(r => r[3] === 1).length, no = ALERTS.filter(r => r[3] === 0).length;
    const unknown = ALERTS.length - yes - no;
    totals('alert', [[`⚠${yes}`, '#ffc21a'], ...(no ? [[String(no), '#8b93a8']] : []),
                     ...(unknown ? [[`?${unknown}`, '#ffc21a', true]] : [])]);
  }
  const hands = HANDINS.filter(r => r[2] === 0).length, backs = HANDINS.length - hands;
  totals('band', S.submit || hands ? [[`✓${hands}`, '#c8d6f5'], ...(backs ? [[`↩${backs}`, hex(TAKEBACK_COLOR)]] : [])] : [['∅', '#8792a8']]);
  const solid = ticks.filter(k => k.solid).length, undated = ticks.filter(k => k.undated).length;
  const hollow = ticks.length - solid - undated;
  if (ticks.length) {
    totals('tick', [[`▲${solid}`, '#ff4d5d'], ...(undated ? [[`◌${undated}`, '#ff4d5d']] : []),
                    ...(hollow ? [[`▵${hollow}`, '#a8616a']] : [])], Math.max(tx0, undatedX + 4));
  }
  if (CHECKS.length) {
    const yes = CHECKS.filter(r => r[2] === 1).length;
    totals('check', [[`◉${yes}`, '#e6eeff'], [`◎${CHECKS.length - yes}`, '#8792a8']]);
  }
}
function drawTimeline() {
  if (!backdrop) buildBackdrop();
  const dpr = timeline.width / timeline.clientWidth;
  const w = timeline.clientWidth;
  tctx.setTransform(1, 0, 0, 1, 0, 0);
  tctx.clearRect(0, 0, timeline.width, timeline.height);
  tctx.drawImage(backdrop, 0, 0);
  tctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const px = trackX(t, w);
  tctx.fillStyle = 'rgba(230,238,255,0.55)';
  tctx.fillRect(TL_LEFT, LANES.band - 1, px - TL_LEFT, 2);
  tctx.fillStyle = 'rgba(255,255,255,0.35)';
  tctx.fillRect(px - 0.5, 1, 1, timeline.clientHeight - 2);
  tctx.fillStyle = '#ffffff';
  tctx.shadowColor = 'rgba(255,255,255,0.8)'; tctx.shadowBlur = 8;
  tctx.beginPath(); tctx.arc(px, LANES.band, 5.5, 0, Math.PI * 2); tctx.fill();
  tctx.shadowBlur = 0;
}
let scrubbing = false;
function scrubTo(e) {
  const rect = timeline.getBoundingClientRect();
  t = Math.min(T, Math.max(0, ((e.clientX - rect.left - TL_LEFT) / (rect.width - TL_LEFT - TL_RIGHT)) * T));
}
timeline.addEventListener('pointerdown', e => { scrubbing = true; timeline.setPointerCapture(e.pointerId); scrubTo(e); });
timeline.addEventListener('pointermove', e => { if (scrubbing) scrubTo(e); });
timeline.addEventListener('pointerup', e => { scrubbing = false; timeline.releasePointerCapture(e.pointerId); });

function resize() {
  renderer.setSize(window.innerWidth, window.innerHeight, false);
  camera.aspect = window.innerWidth / window.innerHeight;
  // Lift the picture clear of the bar at the bottom.
  camera.setViewOffset(window.innerWidth, window.innerHeight, 0, 56, window.innerWidth, window.innerHeight);
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
