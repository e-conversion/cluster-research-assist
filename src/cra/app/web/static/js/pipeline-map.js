// The pipeline map. Inline before, moved out so the CSP can refuse inline scripts.
const STAGES = {
  sources: { label: "Sources", v: "--src" }, build: { label: "Build", v: "--build" },
  cache: { label: "Caches", v: "--cache" }, tools: { label: "Tools", v: "--tool" },
  registry: { label: "Registry", v: "--reg" }, delivery: { label: "Delivery", v: "--deliver" },
};
const NODES = [
  {id:"csv", lane:"sources", n:"data_publication_dois.csv", s:"956 pubs · 148 datasets", d:"Scraped from e-conversion.de/publikationen. The BM25 library foundation — the server refuses to start without it."},
  {id:"enl", lane:"sources", n:"e-conversion-Converted.enl", s:"EndNote · ~1,584 recs", d:"Supplied EndNote library. Primary abstract source; 905 records overlap the library."},
  {id:"scraper", lane:"sources", n:"scraper_cache.json", s:"raw scrape", d:"Raw e-conversion.de scraper output that the DOI list was derived from."},
  {id:"proposal", lane:"sources", n:"EXC_2089…Proposal.pdf", s:"DFG proposal · ~115K tok", d:"The e-conversion 2.0 DFG proposal PDF."},
  {id:"pdfs", lane:"sources", n:"sources/pdfs/", s:"549 collaborator PDFs", d:"Full-text PDFs supplied by a collaborator via institutional access, covering paywalled DOIs the network pipeline can't reach."},
  {id:"members", lane:"sources", n:"e-conversion.de/members", s:"live scrape", d:"PI staff pages, scraped to build the PI cache."},
  {id:"nomad", lane:"sources", n:"NOMAD API", s:"live · uncached", d:"Public NOMAD materials database. Queried live at call time — no local cache."},

  {id:"b_abstracts", lane:"build", n:"build_abstracts_cache.py", s:"CSV+ENL+OpenAlex+S2", d:"Merges library DOIs with EndNote, OpenAlex and Semantic Scholar metadata into the abstracts cache."},
  {id:"b_embed", lane:"build", n:"build_embeddings_cache.py", s:"abstracts → 384-d", d:"Reads abstracts_cache.json and embeds each abstract with BGE-small into a 384-d vector array."},
  {id:"b_fulltext", lane:"build", n:"build_fulltext_cache.py", s:"Unpaywall/PMC/arXiv", d:"Per-DOI network pipeline: arXiv → PMC → repos → publisher PDFs → HTML. Incremental & resumable."},
  {id:"b_ingest", lane:"build", n:"ingest_local_pdfs.py", s:"pdfs/ → fulltext", d:"Merges collaborator PDFs into the full-text cache; never overwrites network hits."},
  {id:"b_pis", lane:"build", n:"build_pis_cache.py", s:"members scrape", d:"Scrapes the members site + per-PI pages into the PI cache."},
  {id:"b_graph", lane:"build", n:"build_graph_cache.py", s:"pis → co-authorship", d:"Reads pis_cache.json and builds the PI co-authorship graph from shared publication DOIs."},
  {id:"b_proposal", lane:"build", n:"extract_proposal_summary.py", s:"proposal → md", d:"Extracts the proposal's Section 2 summary and the whole-document full text as markdown."},

  {id:"c_abstracts", lane:"cache", n:"abstracts_cache.json", s:"~953 abstracts", d:"One entry per DOI: abstract + OpenAlex authors, journal, citation count."},
  {id:"c_embed", lane:"cache", n:"embeddings_cache.npz", s:"~956 × 384-d", d:"BGE-small vectors + a parallel DOI array. Backs semantic search and the publication map."},
  {id:"c_fulltext", lane:"cache", n:"fulltext_cache.json", s:"947 full texts", d:"Full-text bodies keyed by DOI — network hits plus 544 locally-supplied PDFs (99% coverage)."},
  {id:"c_pis", lane:"cache", n:"pis_cache.json", s:"42 PIs", d:"PI profiles: group, department, institution, research focus, and linked publication DOIs."},
  {id:"c_graph", lane:"cache", n:"collaboration_graph.json", s:"co-authorship", d:"PI co-authorship graph (~42 nodes), node-link JSON."},
  {id:"c_propsum", lane:"cache", n:"proposal_summary.md", s:"§2 · ~1.5K tok", d:"Proposal Section 2 — injected into the chat system prompt."},
  {id:"c_propfull", lane:"cache", n:"proposal_fulltext.md", s:"whole · ~115K tok", d:"Whole proposal as markdown; served on demand by get_proposal_fulltext."},

  {id:"t_search", lane:"tools", group:"Search", n:"search_papers", s:"BM25 lexical", d:"Two-stage BM25 search. Best for exact terminology, acronyms, author names."},
  {id:"t_semantic", lane:"tools", group:"Search", n:"semantic_search_papers", s:"BGE cosine", d:"Embedding search over BGE-small vectors. Best for conceptual queries."},
  {id:"t_similar", lane:"tools", group:"Search", n:"get_similar_papers", s:"nearest to a DOI", d:"Nearest papers to a given DOI's own embedding."},
  {id:"t_bydoi", lane:"tools", group:"Papers", n:"get_paper_by_doi", s:"single lookup", d:"Full metadata + abstract for one DOI."},
  {id:"t_fulltext", lane:"tools", group:"Papers", n:"get_paper_fulltext", s:"cached body", d:"Cached full-text markdown for one DOI."},
  {id:"t_list", lane:"tools", group:"Papers", n:"list_papers", s:"filter (AND)", d:"Exhaustive metadata filter by author/year/journal, newest-first."},
  {id:"t_propfull", lane:"tools", group:"Proposal", n:"get_proposal_fulltext", s:"keyword search", d:"Keyword search over proposal paragraphs, top 5."},
  {id:"t_searchpis", lane:"tools", group:"People", n:"search_pis", s:"PI keyword", d:"Keyword search over PIs, top 5."},
  {id:"t_getpi", lane:"tools", group:"People", n:"get_pi", s:"PI profile", d:"Full PI profile + up to 10 linked papers."},
  {id:"t_collab", lane:"tools", group:"Collaboration", n:"get_collaborators", s:"shared pubs", d:"PIs sharing ≥1 publication, most-shared first."},
  {id:"t_joint", lane:"tools", group:"Collaboration", n:"joint_papers", s:"pair → DOIs", d:"DOIs co-authored by two named PIs."},
  {id:"t_centrality", lane:"tools", group:"Collaboration", n:"collaboration_centrality", s:"betweenness", d:"PIs ranked by betweenness centrality."},
  {id:"t_communities", lane:"tools", group:"Collaboration", n:"collaboration_communities", s:"modularity", d:"Greedy-modularity PI communities."},
  {id:"t_nomad", lane:"tools", group:"Materials", n:"search_nomad", s:"live external", d:"Live public NOMAD materials search."},
  {id:"t_status", lane:"tools", group:"Ops", n:"server_status", s:"reads all caches", d:"Health check: cluster, tool count, LLM endpoint, per-cache availability."},
  {id:"t_elab", lane:"tools", group:"Remote (BYOK)", n:"elab_*", s:"eLabFTW ELN", d:"eLabFTW tools fetched per session from the researchmcp proxy. Chat-only; not in the MCP server."},
  {id:"t_dt", lane:"tools", group:"Remote (BYOK)", n:"dt_*", s:"DataTagger", d:"DataTagger tools fetched per session. Chat-only; not in the MCP server."},

  {id:"reg", lane:"registry", n:"FastMCP + openai_tools", s:"single source of truth", d:"Local tools are defined with @mcp.tool() in server.py; the openai_tools bridge derives OpenAI schemas from that registry, so the MCP server and chat app never drift."},

  {id:"d_mcp", lane:"delivery", n:"MCP server (stdio)", s:".mcp.json · econversion", d:"python src/server.py — the 15 local tools over stdio to Claude and other MCP clients."},
  {id:"d_app", lane:"delivery", n:"Web app", s:"Chat · maps", d:"src/web.py + src/static — FastAPI app: streaming chat tool-use loop, publication map, collaboration and pipeline views."},
  {id:"d_llm", lane:"delivery", n:"Multi-provider LLM", s:"gwdg · openrouter", d:"Sidebar provider select; OpenRouter auto-picks the cheapest guardrail-passing model."},
  {id:"d_panel", lane:"delivery", n:"Data-source panel", s:"BYOK eLab/DataTagger", d:"Where a user pastes personal tokens to attach their own lab data to the chat."},
  {id:"d_feedback", lane:"delivery", n:"Feedback form", s:"JSONL log", d:"Per-answer bug-report / general feedback, appended to data/feedback/feedback.jsonl."},
];
const EDGES = [
  ["scraper","csv"],["csv","b_abstracts"],["csv","b_fulltext"],["enl","b_abstracts"],["pdfs","b_ingest"],["members","b_pis"],["proposal","b_proposal"],
  ["b_abstracts","c_abstracts"],["b_embed","c_embed"],["b_fulltext","c_fulltext"],["b_ingest","c_fulltext"],["b_pis","c_pis"],["b_graph","c_graph"],["b_proposal","c_propsum"],["b_proposal","c_propfull"],
  ["c_abstracts","b_embed"],["c_pis","b_graph"],
  ["csv","t_search"],["csv","t_bydoi"],["csv","t_list"],
  ["c_abstracts","t_search"],["c_abstracts","t_semantic"],["c_abstracts","t_bydoi"],["c_abstracts","t_list"],["c_abstracts","t_getpi"],
  ["c_embed","t_semantic"],["c_embed","t_similar"],["c_fulltext","t_fulltext"],["c_propfull","t_propfull"],
  ["c_pis","t_searchpis"],["c_pis","t_getpi"],["c_graph","t_collab"],["c_graph","t_joint"],["c_graph","t_centrality"],["c_graph","t_communities"],["nomad","t_nomad"],
  ["c_embed","d_app"],
  ["t_search","reg"],["t_semantic","reg"],["t_similar","reg"],["t_bydoi","reg"],["t_fulltext","reg"],["t_list","reg"],["t_propfull","reg"],["t_searchpis","reg"],["t_getpi","reg"],["t_collab","reg"],["t_joint","reg"],["t_centrality","reg"],["t_communities","reg"],["t_nomad","reg"],["t_status","reg"],
  ["reg","d_mcp"],["reg","d_app"],["d_panel","t_elab"],["d_panel","t_dt"],["t_elab","d_app"],["t_dt","d_app"],["d_llm","d_app"],["d_app","d_feedback"],
];

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
  .map(s => `<span><i style="background:var(${s.v})"></i>${s.label}</span>`).join("");

const board = document.getElementById("board");
const wires = document.getElementById("wires");
const stageKeys = Object.keys(STAGES);
stageKeys.forEach((key, si) => {
  const meta = STAGES[key];
  const laneNodes = NODES.filter(n => n.lane === key);
  const band = document.createElement("div");
  band.className = "band"; band.style.setProperty("--stage", `var(${meta.v})`);
  band.innerHTML = `<div class="bh"><b></b>${meta.label}<span class="c">${laneNodes.length} node${laneNodes.length > 1 ? "s" : ""}</span></div>`;
  const body = document.createElement("div"); body.className = "body";
  let g = null;
  laneNodes.forEach(n => {
    if (n.group && n.group !== g) { const gl = document.createElement("div"); gl.className = "glab"; gl.textContent = n.group; body.appendChild(gl); g = n.group; }
    const card = document.createElement("div");
    card.className = "node"; card.dataset.id = n.id; card.style.setProperty("--stage", `var(${meta.v})`);
    card.innerHTML = `<div class="nm">${n.n}</div><div class="sb">${n.s}</div>`;
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
  const mk = ids => ids.length ? `<ul>${ids.map(x => `<li>${byId[x].n}</li>`).join("")}</ul>` : `<span class="none">nothing</span>`;
  detail.innerHTML = `
    <div class="dh"><span class="chip" style="background:${col}">${STAGES[n.lane].label}</span><span class="dn">${n.n}</span><span class="dsub">${n.s}</span></div>
    <p class="dd">${n.d}</p>
    <div class="cols"><div><h4>Fed by</h4>${mk(parents[id])}</div><div><h4>Feeds</h4>${mk(children[id])}</div></div>`;
}
addEventListener("resize", () => { if (cur) drawWires(cur); });
