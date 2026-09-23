// The collaboration graph page. Inline before, moved out so the CSP can refuse inline scripts.
// Served by the FastAPI app: the graph comes from its API (relative URL, so
// the page works under any prefix). Everything below runs once it has loaded.
fetch("../api/collaboration-graph").then((r) => r.ok ? r.json() : r.json().then((e) => Promise.reject(new Error(e.hint || e.error))))
.then(({ nodes: NODES, links: LINKS }) => {
const INST = { TUM:{k:"tum",l:"TUM",n:"Technical University of Munich"}, LMU:{k:"lmu",l:"LMU",n:"Ludwig-Maximilians-Universität"},
  FHI:{k:"fhi",l:"FHI",n:"Fritz Haber Institute"}, "MPI FKF":{k:"mpi",l:"MPI",n:"Max Planck Institute FKF"} };
const instK = n => INST[n.inst] ? INST[n.inst].k : "other";
const cssv = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const col = n => cssv("--" + instK(n));

const byId = Object.fromEntries(NODES.map(n => [n.id, n]));
NODES.forEach(n => n.co = []);
LINKS.forEach(l => { byId[l.source].co.push({ id: l.target, w: l.weight }); byId[l.target].co.push({ id: l.source, w: l.weight }); });
const adj = {}; NODES.forEach(n => adj[n.id] = new Set([n.id]));
LINKS.forEach(l => { adj[l.source].add(l.target); adj[l.target].add(l.source); });
const rOf = n => 5 + Math.sqrt(n.deg) * 3;
const maxW = Math.max(...LINKS.map(l => l.weight), 1);
const swOf = w => 0.7 + Math.sqrt(w / maxW) * 3.6;

document.getElementById("hint").textContent = NODES.length + " PIs · " + LINKS.length + " collaborating pairs";
const present = [...new Set(NODES.map(n => n.inst))];
const legOrder = ["TUM","LMU","FHI","MPI FKF"].filter(i => present.includes(i));
if (present.some(i => !INST[i])) legOrder.push("__o");
document.getElementById("legend").innerHTML = legOrder.map(i => {
  const k = INST[i] ? INST[i].k : "other", l = INST[i] ? INST[i].l : "Other";
  return `<span><i style="background:var(--${k})"></i>${l}</span>`;
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
node.append("circle").attr("r", rOf).attr("fill", d => d.deg ? col(d) : "var(--other)").attr("fill-opacity", d => d.deg ? 1 : .5);
node.append("text").attr("class", "lab").attr("text-anchor", "middle").attr("dy", d => -rOf(d) - 4).text(d => d.label);
node.call(d3.drag()
  .on("start", (e, d) => { if (!e.active) sim.alphaTarget(.3).restart(); d.fx = d.x; d.fy = d.y; svg.node().classList.add("grab"); })
  .on("drag", (e, d) => { d.fx = e.x; d.fy = e.y; })
  .on("end", (e, d) => { if (!e.active) sim.alphaTarget(0); d.fx = null; d.fy = null; svg.node().classList.remove("grab"); }));

const sim = d3.forceSimulation(NODES)
  .force("link", d3.forceLink(LINKS).id(d => d.id).distance(d => 46 + 26 / Math.sqrt(d.weight)).strength(.28))
  .force("charge", d3.forceManyBody().strength(-360))
  .force("collide", d3.forceCollide().radius(d => rOf(d) + 8).strength(.9))
  .force("center", d3.forceCenter(W / 2, H / 2))
  .force("x", d3.forceX(W / 2).strength(.035)).force("y", d3.forceY(H / 2).strength(.05))
  .on("tick", ticked);
function ticked() {
  link.attr("d", d => {
    const dx = d.target.x - d.source.x, dy = d.target.y - d.source.y, dr = Math.hypot(dx, dy) * 1.9;
    return `M${d.source.x},${d.source.y}A${dr},${dr} 0 0,1 ${d.target.x},${d.target.y}`;
  });
  node.attr("transform", d => `translate(${d.x},${d.y})`);
}
svg.call(d3.zoom().scaleExtent([.4, 4]).on("zoom", e => root.attr("transform", e.transform))).on("dblclick.zoom", null);

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
    .map(x => `<li><span class="co">${byId[x.id].name.replace(/^(Prof\.|Dr\.|Prof\. Dr\.)\s*/, "")}</span><span class="w">${x.w} paper${x.w === 1 ? "" : "s"}</span></li>`).join("");
  card.innerHTML = `<div class="cinst" style="color:${c}">${meta ? meta.l + " · " + meta.n : (n.inst || "—")}</div>
    <h3>${n.name}</h3><div class="grp">${n.group || "—"}</div>
    <div class="nums"><div><div class="n" style="color:${c}">${n.deg}</div><div class="l">collaborator${n.deg === 1 ? "" : "s"}</div></div>
      <div><div class="n">${n.papers}</div><div class="l">papers</div></div></div>
    ${n.deg ? `<h4>Most shared with</h4><ul>${tops}</ul>` : `<div class="hintline">No co-authorships recorded in the library.</div>`}`;
  card.classList.add("on");
}
// settle the layout before first paint so it opens at rest, not mid-jitter
for (let i = 0; i < 260; i++) sim.tick();
ticked();
addEventListener("resize", () => { W = frame.clientWidth; H = frame.clientHeight; svg.attr("viewBox", [0, 0, W, H]); sim.force("center", d3.forceCenter(W / 2, H / 2)).alpha(.2).restart(); });
}).catch((err) => { document.getElementById("hint").textContent = err.message || "Collaboration graph not built. Run: python build.py graph"; });
