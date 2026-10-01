// PI co-authorship network, drawn with d3 in the page itself: one document
// scrolls, pinches and follows the theme, which a framed page did not.
import { getJSON } from "../api.js";
import { escapeHtml } from "../markdown.js";
import { expandable, loadScript } from "./viz.js";

const D3_URL = "https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js";
// pinned build; bump both together
const D3_INTEGRITY = "sha384-CjloA8y00+1SDAUkjs099PVfnY2KmDC2BZnws9kh8D/lX1s46w6EPhpXdqMfjK6i";

/** A ranked name without its titles: "Prof. Dr. Ada Lovelace" -> "Ada Lovelace". */
const untitled = (name) => name.replace(/^(Prof\.|Dr\.|Prof\. Dr\.)\s*/, "");

export function collaborationView() {
  return {
    mount(container) {
      container.innerHTML = `
        <div class="page">
          <h1>Collaboration Graph</h1>
          <p class="lede">PI co-authorship network from the cluster's publications — nodes are PIs (sized by number
            of collaborators, colored by institution), edges are shared papers (thicker = more). Click or tap a PI to
            isolate who they publish with.</p>
          <div class="collab-bar">
            <div class="legend" id="graph-legend"></div>
            <div class="encodes"><b>dot size</b> = collaborators &middot; <b>line weight</b> = shared papers</div>
          </div>
          <div class="viz-frame graph-frame">
            <svg role="img" aria-label="Co-authorship network of the principal investigators"></svg>
            <button type="button" class="reset">Reset</button>
            <div class="graph-card" aria-live="polite"></div>
            <div class="hint">Loading…</div>
          </div>
        </div>`;
      const frame = container.querySelector(".graph-frame");
      const hint = frame.querySelector(".hint");
      const unexpand = expandable(frame);
      let teardown = () => {};
      let disposed = false;

      Promise.all([
        getJSON("api/collaboration-graph"),
        // which institutions the legend tells apart comes from the deployment's brand
        getJSON("brand/brand.json").catch(() => ({ institutions: [] })),
        loadScript(D3_URL, D3_INTEGRITY, "d3"),
      ]).then(([graph, brand]) => {
        if (!disposed) teardown = draw(frame, container.querySelector("#graph-legend"), graph, brand.institutions || []);
      }).catch((e) => { hint.textContent = e.data?.hint || e.message || "Collaboration graph not available."; });

      return () => { disposed = true; unexpand(); teardown(); };
    },
  };
}

/** Lay out and draw the network; returns the cleanup. */
function draw(frame, legendEl, { nodes: NODES, links: LINKS }, institutions) {
  const d3 = window.d3;
  // each named institution takes the next colour of the categorical palette;
  // fills name the token, so a theme switch recolours without a redraw
  const INST = Object.fromEntries(institutions.map((it, i) => [it.key, { k: `viz-${(i % 8) + 1}`, l: it.label, n: it.name }]));
  const col = (n) => `var(--${INST[n.inst] ? INST[n.inst].k : "viz-other"})`;

  const byId = Object.fromEntries(NODES.map((n) => [n.id, n]));
  NODES.forEach((n) => { n.co = []; });
  LINKS.forEach((l) => { byId[l.source].co.push({ id: l.target, w: l.weight }); byId[l.target].co.push({ id: l.source, w: l.weight }); });
  const adj = {}; NODES.forEach((n) => { adj[n.id] = new Set([n.id]); });
  LINKS.forEach((l) => { adj[l.source].add(l.target); adj[l.target].add(l.source); });
  const rOf = (n) => 5.5 + Math.sqrt(n.deg) * 3;
  const maxW = Math.max(...LINKS.map((l) => l.weight), 1);
  const swOf = (w) => 0.7 + Math.sqrt(w / maxW) * 3.6;

  const hint = frame.querySelector(".hint");
  hint.textContent = `${NODES.length} PIs · ${LINKS.length} collaborating pairs`;
  const present = [...new Set(NODES.map((n) => n.inst))];
  const legOrder = Object.keys(INST).filter((i) => present.includes(i));
  if (present.some((i) => !INST[i])) legOrder.push("__o");
  legendEl.innerHTML = legOrder.map((i) => {
    const k = INST[i] ? INST[i].k : "viz-other", l = INST[i] ? INST[i].l : "Other";
    return `<span><i style="background:var(--${k})"></i>${escapeHtml(l)}</span>`;
  }).join("");

  const svgEl = frame.querySelector("svg");
  const svg = d3.select(svgEl);
  let W = frame.clientWidth, H = frame.clientHeight;
  svg.attr("viewBox", [0, 0, W, H]);
  const root = svg.append("g");
  const gL = root.append("g"), gN = root.append("g");

  const link = gL.selectAll("path").data(LINKS).join("path")
    .attr("class", "link").attr("stroke-width", (d) => swOf(d.weight)).attr("stroke-opacity", 0.55);

  const node = gN.selectAll("g").data(NODES).join("g").attr("class", "node");
  node.append("circle").attr("r", rOf).style("fill", (d) => (d.deg ? col(d) : "var(--viz-other)")).attr("fill-opacity", (d) => (d.deg ? 1 : 0.5));
  node.append("text").attr("class", "lab").attr("text-anchor", "middle").attr("dy", (d) => -rOf(d) - 4).text((d) => d.label);
  // The simulation wakes on the first move, not on the press: a tap that
  // only selects a PI leaves the picture still.
  node.call(d3.drag()
    .on("start", (e, d) => { d.fx = d.x; d.fy = d.y; d.moved = false; })
    .on("drag", (e, d) => {
      if (!d.moved) { d.moved = true; sim.alphaTarget(0.3).restart(); svgEl.classList.add("grab"); }
      d.fx = e.x; d.fy = e.y;
    })
    .on("end", (e, d) => {
      if (d.moved && !e.active) sim.alphaTarget(0);
      d.fx = null; d.fy = null; svgEl.classList.remove("grab");
    }));

  // PIs without a recorded co-authorship would otherwise be flung to the rim by
  // the same repulsion that spreads the network, and the whole picture would
  // have to shrink to keep them on screen. They repel little and gather in a
  // loose group beside the network instead, wherever it ends up: to its right
  // in a wide frame, below it in a tall one such as a phone's.
  const alone = (d) => !d.deg;
  const linked = NODES.filter((d) => !alone(d)), loners = NODES.filter(alone);
  function forcePen(alpha) {
    if (!linked.length || !loners.length) return;
    let maxX = -Infinity, maxY = -Infinity, sumX = 0, sumY = 0;
    for (const n of linked) { maxX = Math.max(maxX, n.x); maxY = Math.max(maxY, n.y); sumX += n.x; sumY += n.y; }
    const [tx, ty] = H > W
      ? [sumX / linked.length, maxY + 110]
      : [maxX + 120, sumY / linked.length + 40];
    for (const n of loners) { n.vx += (tx - n.x) * 0.22 * alpha; n.vy += (ty - n.y) * 0.22 * alpha; }
  }
  // Labels sit above the dots at page text size, so neighbours must clear half a
  // name to each side; the radius grows with the name, not just the dot.
  const room = (d) => Math.max(rOf(d) + 10, d.label.length * 3.4 + 6);
  const centre = () => sim
    .force("x", d3.forceX(W / 2).strength((d) => (alone(d) ? 0 : 0.05)))
    .force("y", d3.forceY(H / 2).strength((d) => (alone(d) ? 0 : 0.07)));
  const sim = d3.forceSimulation(NODES)
    .force("link", d3.forceLink(LINKS).id((d) => d.id).distance((d) => 44 + 22 / Math.sqrt(d.weight)).strength(0.3))
    .force("charge", d3.forceManyBody().strength((d) => (alone(d) ? -30 : -200)))
    .force("collide", d3.forceCollide().radius(room).strength(0.85))
    .force("pen", forcePen)
    .on("tick", ticked);
  centre();
  function ticked() {
    link.attr("d", (d) => {
      const dx = d.target.x - d.source.x, dy = d.target.y - d.source.y, dr = Math.hypot(dx, dy) * 1.9;
      return `M${d.source.x},${d.source.y}A${dr},${dr} 0 0,1 ${d.target.x},${d.target.y}`;
    });
    node.attr("transform", (d) => `translate(${d.x},${d.y})`);
  }
  // A pinch or a wheel zooms the graph, never the page around it.
  const zoom = d3.zoom().scaleExtent([0.3, 4]).on("zoom", (e) => root.attr("transform", e.transform));
  svg.call(zoom).on("dblclick.zoom", null);
  // The default view frames every node, labels included, clear of the buttons
  // along the top edge and the count along the bottom; it never magnifies a
  // small graph, only shrinks one that has spread past the frame.
  const EDGE = { top: 48, bottom: 28, side: 8 };
  function fitAll(animate) {
    // a label is centred over its dot and about room() wide on either side
    const x0 = Math.min(...NODES.map((n) => n.x - room(n))), x1 = Math.max(...NODES.map((n) => n.x + room(n)));
    const y0 = Math.min(...NODES.map((n) => n.y - rOf(n) - 18)), y1 = Math.max(...NODES.map((n) => n.y + rOf(n)));
    const w = W - 2 * EDGE.side, h = H - EDGE.top - EDGE.bottom;
    const k = Math.min(1, w / (x1 - x0), h / (y1 - y0));
    const t = d3.zoomIdentity
      .translate(EDGE.side + w / 2 - k * (x0 + x1) / 2, EDGE.top + h / 2 - k * (y0 + y1) / 2).scale(k);
    (animate ? svg.transition().duration(450) : svg).call(zoom.transform, t);
  }

  const card = frame.querySelector(".graph-card"), resetBtn = frame.querySelector(".reset");
  let pin = null;
  function apply(id) {
    const near = id ? adj[id] : null;
    node.classed("dim", (d) => id && !near.has(d.id));
    node.select(".lab").classed("on", (d) => id && near.has(d.id));
    link.classed("dim", (d) => id && d.source.id !== id && d.target.id !== id)
      .style("stroke", (d) => (id && (d.source.id === id || d.target.id === id) ? col(byId[id]) : null))
      .attr("stroke-opacity", (d) => (!id ? 0.55 : (d.source.id === id || d.target.id === id ? 0.95 : 0.06)));
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
      .map((x) => `<li><span class="co">${escapeHtml(untitled(byId[x.id].name))}</span><span class="w">${x.w} paper${x.w === 1 ? "" : "s"}</span></li>`).join("");
    // a pinned card can be closed where it is; a hovered one goes with the pointer
    const close = pin ? '<button type="button" class="close" aria-label="Close">✕</button>' : "";
    card.innerHTML = `${close}<div class="cinst" style="color:${c}">${escapeHtml(meta ? [meta.l, meta.n].filter(Boolean).join(" · ") : (n.inst || "—"))}</div>
      <h3>${escapeHtml(n.name)}</h3><div class="grp">${escapeHtml(n.group || "—")}</div>
      <div class="nums"><div><div class="n" style="color:${c}">${n.deg}</div><div class="l">collaborator${n.deg === 1 ? "" : "s"}</div></div>
        <div><div class="n">${n.papers}</div><div class="l">papers</div></div></div>
      ${n.deg ? `<h4>Most shared with</h4><ul>${tops}</ul>` : '<div class="hintline">No co-authorships recorded in the library.</div>'}`;
    card.querySelector(".close")?.addEventListener("click", () => { pin = null; apply(null); });
    card.classList.add("on");
  }
  // settle the layout before first paint so it opens at rest, not mid-jitter
  for (let i = 0; i < 260; i++) sim.tick();
  ticked();
  fitAll(false);

  // The frame changes size with the window, a phone's rotation and the expand
  // button; the network re-centres, and turns its loners below or beside it.
  const sizer = new ResizeObserver(() => {
    const w = frame.clientWidth, h = frame.clientHeight;
    if (!w || !h || (w === W && h === H)) return;
    W = w; H = h;
    svg.attr("viewBox", [0, 0, W, H]);
    centre().alpha(0.2).restart();
    fitAll(true);
  });
  sizer.observe(frame);
  return () => { sizer.disconnect(); sim.stop(); };
}
