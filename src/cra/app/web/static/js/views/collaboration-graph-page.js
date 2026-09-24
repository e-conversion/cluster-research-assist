// The collaboration graph page. Inline before, moved out so the CSP can refuse inline scripts.
// Served by the web app: the graph comes from its API (relative URL, so
// the page works under any prefix). Everything below runs once it has loaded.
const graph = fetch("../api/collaboration-graph").then((r) => r.ok ? r.json() : r.json().then((e) => Promise.reject(new Error(e.hint || e.error))));
// which institutions the legend tells apart comes from the deployment's brand
const brand = fetch("../brand/brand.json").then((r) => r.json()).catch(() => ({ institutions: [] }));
const esc = (t) => String(t).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
Promise.all([graph, brand]).then(([{ nodes: NODES, links: LINKS }, { institutions }]) => {
// each named institution takes the next colour of the categorical palette
const INST = Object.fromEntries(institutions.map((it, i) => [it.key, { k: `viz-${(i % 8) + 1}`, l: it.label, n: it.name }]));
const instK = n => INST[n.inst] ? INST[n.inst].k : "viz-other";
const cssv = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const col = n => cssv("--" + instK(n));

const byId = Object.fromEntries(NODES.map(n => [n.id, n]));
NODES.forEach(n => n.co = []);
LINKS.forEach(l => { byId[l.source].co.push({ id: l.target, w: l.weight }); byId[l.target].co.push({ id: l.source, w: l.weight }); });
const adj = {}; NODES.forEach(n => adj[n.id] = new Set([n.id]));
LINKS.forEach(l => { adj[l.source].add(l.target); adj[l.target].add(l.source); });
const rOf = n => 5.5 + Math.sqrt(n.deg) * 3;
const maxW = Math.max(...LINKS.map(l => l.weight), 1);
const swOf = w => 0.7 + Math.sqrt(w / maxW) * 3.6;

document.getElementById("hint").textContent = NODES.length + " PIs · " + LINKS.length + " collaborating pairs";
const present = [...new Set(NODES.map(n => n.inst))];
const legOrder = Object.keys(INST).filter(i => present.includes(i));
if (present.some(i => !INST[i])) legOrder.push("__o");
document.getElementById("legend").innerHTML = legOrder.map(i => {
  const k = INST[i] ? INST[i].k : "viz-other", l = INST[i] ? INST[i].l : "Other";
  return `<span><i style="background:var(--${k})"></i>${esc(l)}</span>`;
}).join("");

const frame = document.querySelector(".frame");
const svg = d3.select("#graph");
let W = frame.clientWidth, H = frame.clientHeight;
svg.attr("viewBox", [0, 0, W, H]);
const root = svg.append("g");
const gL = root.append("g"), gN = root.append("g");

const link = gL.selectAll("path").data(LINKS).join("path")
  .attr("class", "link").attr("stroke-width", d => swOf(d.weight)).attr("stroke-opacity", .55);

const node = gN.selectAll("g").data(NODES).join("g").attr("class", "node");
node.append("circle").attr("r", rOf).attr("fill", d => d.deg ? col(d) : "var(--viz-other)").attr("fill-opacity", d => d.deg ? 1 : .5);
node.append("text").attr("class", "lab").attr("text-anchor", "middle").attr("dy", d => -rOf(d) - 4).text(d => d.label);
node.call(d3.drag()
  .on("start", (e, d) => { if (!e.active) sim.alphaTarget(.3).restart(); d.fx = d.x; d.fy = d.y; svg.node().classList.add("grab"); })
  .on("drag", (e, d) => { d.fx = e.x; d.fy = e.y; })
  .on("end", (e, d) => { if (!e.active) sim.alphaTarget(0); d.fx = null; d.fy = null; svg.node().classList.remove("grab"); }));

// PIs without a recorded co-authorship would otherwise be flung to the rim by
// the same repulsion that spreads the network, and the whole picture would
// have to shrink to keep them on screen. They repel little and gather in a
// loose group just right of the network instead, wherever it ends up.
const alone = d => !d.deg;
const linked = NODES.filter(d => !alone(d)), loners = NODES.filter(alone);
function forcePen(alpha) {
  if (!linked.length || !loners.length) return;
  let maxX = -Infinity, sumY = 0;
  for (const n of linked) { if (n.x > maxX) maxX = n.x; sumY += n.y; }
  const tx = maxX + 120, ty = sumY / linked.length + 40;
  for (const n of loners) { n.vx += (tx - n.x) * .22 * alpha; n.vy += (ty - n.y) * .22 * alpha; }
}
// Labels sit above the dots at page text size, so neighbours must clear half a
// name to each side; the radius grows with the name, not just the dot.
const room = d => Math.max(rOf(d) + 10, d.label.length * 3.4 + 6);
const sim = d3.forceSimulation(NODES)
  .force("link", d3.forceLink(LINKS).id(d => d.id).distance(d => 44 + 22 / Math.sqrt(d.weight)).strength(.3))
  .force("charge", d3.forceManyBody().strength(d => alone(d) ? -30 : -200))
  .force("collide", d3.forceCollide().radius(room).strength(.85))
  .force("pen", forcePen)
  .force("x", d3.forceX(W / 2).strength(d => alone(d) ? 0 : .05))
  .force("y", d3.forceY(H / 2).strength(d => alone(d) ? 0 : .07))
  .on("tick", ticked);
function ticked() {
  link.attr("d", d => {
    const dx = d.target.x - d.source.x, dy = d.target.y - d.source.y, dr = Math.hypot(dx, dy) * 1.9;
    return `M${d.source.x},${d.source.y}A${dr},${dr} 0 0,1 ${d.target.x},${d.target.y}`;
  });
  node.attr("transform", d => `translate(${d.x},${d.y})`);
}
const zoom = d3.zoom().scaleExtent([.3, 4]).on("zoom", e => root.attr("transform", e.transform));
svg.call(zoom).on("dblclick.zoom", null);
// The default view frames every node, labels included; it never magnifies a
// small graph, only shrinks one that has spread past the frame.
function fitAll(animate) {
  const pad = 30;
  const xs = NODES.map(n => n.x), ys = NODES.map(n => n.y);
  const x0 = Math.min(...xs) - pad, x1 = Math.max(...xs) + pad;
  const y0 = Math.min(...ys) - pad - 16, y1 = Math.max(...ys) + pad;
  const k = Math.min(1, W / (x1 - x0), H / (y1 - y0));
  const t = d3.zoomIdentity.translate(W / 2 - k * (x0 + x1) / 2, H / 2 - k * (y0 + y1) / 2).scale(k);
  (animate ? svg.transition().duration(450) : svg).call(zoom.transform, t);
}

const card = document.getElementById("card"), resetBtn = document.getElementById("reset");
let pin = null;
function apply(id) {
  const near = id ? adj[id] : null;
  node.classed("dim", d => id && !near.has(d.id));
  node.select(".lab").classed("on", d => id && near.has(d.id));
  link.classed("dim", d => id && d.source.id !== id && d.target.id !== id)
      .attr("stroke", d => id && (d.source.id === id || d.target.id === id) ? col(byId[id]) : "var(--edge)")
      .attr("stroke-opacity", d => !id ? .55 : (d.source.id === id || d.target.id === id ? .95 : .06));
  resetBtn.classList.toggle("on", !!id);
  if (id) showCard(id); else card.classList.remove("on");
}
node.on("mouseenter", (e, d) => { if (!pin) apply(d.id); })
    .on("click", (e, d) => { e.stopPropagation(); pin = pin === d.id ? null : d.id; apply(pin); });
svg.on("mouseleave", () => apply(pin));
svg.on("click", () => { pin = null; apply(null); });
resetBtn.addEventListener("click", () => { pin = null; apply(null); });

function showCard(id) {
  const n = byId[id], meta = INST[n.inst], c = col(n);
  const tops = [...n.co].sort((a, b) => b.w - a.w).slice(0, 5)
    .map(x => `<li><span class="co">${esc(byId[x.id].name.replace(/^(Prof\.|Dr\.|Prof\. Dr\.)\s*/, ""))}</span><span class="w">${x.w} paper${x.w === 1 ? "" : "s"}</span></li>`).join("");
  card.innerHTML = `<div class="cinst" style="color:${c}">${esc(meta ? [meta.l, meta.n].filter(Boolean).join(" · ") : (n.inst || "—"))}</div>
    <h3>${esc(n.name)}</h3><div class="grp">${esc(n.group || "—")}</div>
    <div class="nums"><div><div class="n" style="color:${c}">${n.deg}</div><div class="l">collaborator${n.deg === 1 ? "" : "s"}</div></div>
      <div><div class="n">${n.papers}</div><div class="l">papers</div></div></div>
    ${n.deg ? `<h4>Most shared with</h4><ul>${tops}</ul>` : `<div class="hintline">No co-authorships recorded in the library.</div>`}`;
  card.classList.add("on");
}
// settle the layout before first paint so it opens at rest, not mid-jitter
for (let i = 0; i < 260; i++) sim.tick();
ticked();
fitAll(false);
addEventListener("resize", () => {
  W = frame.clientWidth; H = frame.clientHeight; svg.attr("viewBox", [0, 0, W, H]);
  sim.force("x", d3.forceX(W / 2).strength(d => alone(d) ? 0 : .05)).force("y", d3.forceY(H / 2).strength(d => alone(d) ? 0 : .07)).alpha(.2).restart();
  fitAll(true);
});
}).catch((err) => { document.getElementById("hint").textContent = err.message || "Collaboration graph not built. Run: python build.py graph"; });
