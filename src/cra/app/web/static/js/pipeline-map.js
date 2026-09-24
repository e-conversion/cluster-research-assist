// The pipeline map. A deployment describes its own pipeline in brand.json;
// this page only draws it. Moved out of the page so the CSP can refuse inline scripts.
const esc = (t) => String(t).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

fetch("../brand/brand.json").then((r) => r.json()).then(({ pipeline }) => {
if (!pipeline) return;
// stages take the categorical palette in order, so a theme recolours them
const STAGES = Object.fromEntries(pipeline.stages.map((s, i) => [s.key, { label: s.label, v: `--viz-${(i % 8) + 1}` }]));
const NODES = pipeline.nodes.map((n) => ({ id: n.id, lane: n.stage, group: n.group, n: n.name, s: n.summary, d: n.detail }));
const EDGES = pipeline.edges;
document.getElementById("intro").textContent = pipeline.intro;

const byId = Object.fromEntries(NODES.map(n => [n.id, n]));
const parents = {}, children = {};
NODES.forEach(n => { parents[n.id] = []; children[n.id] = []; });
EDGES.forEach(([a, b]) => { children[a].push(b); parents[b].push(a); });
// every box upstream of id, traced all the way back to the raw sources (excludes id)
function ancestorsOf(id) {
  const set = new Set(), st = [id];
  while (st.length) { const x = st.pop(); parents[x].forEach(p => { if (!set.has(p)) { set.add(p); st.push(p); } }); }
  return set;
}
const cssv = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const stageCol = lane => cssv(STAGES[lane].v);

document.getElementById("legend").innerHTML = Object.values(STAGES)
  .map(s => `<span><i style="background:var(${s.v})"></i>${esc(s.label)}</span>`).join("");

const board = document.getElementById("board");
const wires = document.getElementById("wires");
const stageKeys = Object.keys(STAGES);
stageKeys.forEach((key, si) => {
  const meta = STAGES[key];
  const laneNodes = NODES.filter(n => n.lane === key);
  const band = document.createElement("div");
  band.className = "band"; band.style.setProperty("--stage", `var(${meta.v})`);
  band.innerHTML = `<div class="bh"><b></b>${esc(meta.label)}<span class="c">${laneNodes.length} node${laneNodes.length > 1 ? "s" : ""}</span></div>`;
  const body = document.createElement("div"); body.className = "body";
  let g = null;
  laneNodes.forEach(n => {
    if (n.group && n.group !== g) { const gl = document.createElement("div"); gl.className = "glab"; gl.textContent = n.group; body.appendChild(gl); g = n.group; }
    const card = document.createElement("div");
    card.className = "node"; card.dataset.id = n.id; card.style.setProperty("--stage", `var(${meta.v})`);
    card.innerHTML = `<div class="nm">${esc(n.n)}</div><div class="sb">${esc(n.s)}</div>`;
    card.addEventListener("click", e => { e.stopPropagation(); select(n.id); });
    body.appendChild(card);
  });
  band.appendChild(body); board.appendChild(band);
  if (si < stageKeys.length - 1) {
    const flow = document.createElement("div"); flow.className = "flow";
    flow.innerHTML = `<svg viewBox="0 0 22 24"><line x1="11" y1="1" x2="11" y2="16"/><path d="M11 23 L7 15 L15 15 Z"/></svg>`;
    board.appendChild(flow);
  }
});

const detail = document.getElementById("detail");
const resetBtn = document.getElementById("reset");
const INTRO = detail.innerHTML;
let cur = null;

function select(id) {
  if (cur === id) return clear();
  cur = id;
  // Full upstream provenance + direct output: light every ancestor back to the
  // sources, plus only what this node directly produces (one hop forward) — so a
  // build script shows the cache it makes but never bounces into a sibling script.
  const lit = new Set([id, ...ancestorsOf(id), ...children[id]]);
  board.classList.add("on");
  document.querySelectorAll(".node").forEach(el => {
    el.classList.toggle("lit", lit.has(el.dataset.id));
    el.classList.toggle("focus", el.dataset.id === id);
  });
  resetBtn.classList.add("on");
  renderDetail(id);
  drawWires(id);
}
function clear() {
  cur = null; board.classList.remove("on");
  document.querySelectorAll(".node").forEach(el => el.classList.remove("lit", "focus"));
  resetBtn.classList.remove("on"); detail.innerHTML = INTRO; wires.innerHTML = "";
}
resetBtn.addEventListener("click", clear);
document.addEventListener("keydown", e => e.key === "Escape" && clear());
document.getElementById("board").addEventListener("click", e => { if (!e.target.closest(".node")) clear(); });

function drawWires(id) {
  const br = board.getBoundingClientRect();
  wires.setAttribute("viewBox", `0 0 ${br.width} ${br.height}`);
  wires.setAttribute("width", br.width); wires.setAttribute("height", br.height);
  const card = {}; document.querySelectorAll(".node").forEach(el => card[el.dataset.id] = el);
  let s = "";
  // upstream provenance tree (edges among id + its ancestors) plus this node's
  // direct outputs — matches the highlighted set, no downstream spidering
  const up = new Set([id, ...ancestorsOf(id)]);
  const draw = EDGES.filter(([a, b]) => (up.has(a) && up.has(b)) || a === id);
  draw.forEach(([a, b]) => {
    const ra = card[a].getBoundingClientRect(), rb = card[b].getBoundingClientRect();
    const ax = ra.left - br.left + ra.width / 2, ay = ra.top - br.top + ra.height / 2;
    const bx = rb.left - br.left + rb.width / 2, by = rb.top - br.top + rb.height / 2;
    const ecol = stageCol(byId[a].lane);
    let sx, sy, ex, ey, c1x, c1y, c2x, c2y, hx, hy;
    if (Math.abs(by - ay) >= Math.abs(bx - ax)) {           // vertical (cross-stage)
      const down = by >= ay;
      sx = ax; sy = down ? ra.bottom - br.top : ra.top - br.top;
      ex = bx; ey = down ? rb.top - br.top : rb.bottom - br.top;
      const k = (ey - sy) * 0.5; c1x = sx; c1y = sy + k; c2x = ex; c2y = ey - k; hx = 0; hy = down ? 1 : -1;
    } else {                                                  // horizontal (within a stage)
      const right = bx >= ax;
      sx = right ? ra.right - br.left : ra.left - br.left; sy = ay;
      ex = right ? rb.left - br.left : rb.right - br.left; ey = by;
      const k = (ex - sx) * 0.5; c1x = sx + k; c1y = sy; c2x = ex - k; c2y = ey; hx = right ? 1 : -1; hy = 0;
    }
    s += `<path class="wire" d="M ${sx} ${sy} C ${c1x} ${c1y} ${c2x} ${c2y} ${ex} ${ey}" stroke="${ecol}" stroke-opacity="0.92"/>`;
    const b1x = ex - 8 * hx - 4.5 * hy, b1y = ey - 8 * hy + 4.5 * hx;
    const b2x = ex - 8 * hx + 4.5 * hy, b2y = ey - 8 * hy - 4.5 * hx;
    s += `<path class="whead" fill="${ecol}" d="M ${ex} ${ey} L ${b1x} ${b1y} L ${b2x} ${b2y} Z"/>`;
  });
  wires.innerHTML = s;
}

function renderDetail(id) {
  const n = byId[id], col = `var(${STAGES[n.lane].v})`;
  const mk = ids => ids.length ? `<ul>${ids.map(x => `<li>${esc(byId[x].n)}</li>`).join("")}</ul>` : `<span class="none">nothing</span>`;
  detail.innerHTML = `
    <div class="dh"><span class="chip" style="background:${col}">${esc(STAGES[n.lane].label)}</span><span class="dn">${esc(n.n)}</span><span class="dsub">${esc(n.s)}</span></div>
    <p class="dd">${esc(n.d)}</p>
    <div class="cols"><div><h4>Fed by</h4>${mk(parents[id])}</div><div><h4>Feeds</h4>${mk(children[id])}</div></div>`;
}
addEventListener("resize", () => { if (cur) drawWires(cur); });
});
