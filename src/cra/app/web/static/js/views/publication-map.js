// Library map: UMAP layout of the paper embeddings rendered with deck.gl.
import { getJSON } from "../api.js";

const DECK_URL = "https://cdn.jsdelivr.net/npm/deck.gl@9.0.38/dist.min.js";
// pinned build; bump both together
const DECK_INTEGRITY = "sha384-tEG529toczQRv/bqd2PyHy5vTO49FMjQri0c1oB44gSkYiNouZz8L9yNTVx0YGl+";
let deckLoading = null;
function loadDeck() {
  if (window.deck) return Promise.resolve();
  if (!deckLoading) deckLoading = new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = DECK_URL; s.integrity = DECK_INTEGRITY; s.crossOrigin = "anonymous";
    s.onload = resolve; s.onerror = () => reject(new Error("deck.gl failed to load"));
    document.head.append(s);
  });
  return deckLoading;
}

const cache = new Map(); // clusters -> payload (server memoises too; this saves the round-trip)

// Title labels: footprint of one label on screen, and how far above the
// fitted view the first titles appear and the last ones are handed out.
const LABEL = { w: 330, h: 22, size: 11.5, chars: 56, offset: 9 };
const FIRST_TITLES_AT = 0.75, LAST_TITLES_AT = 8, TITLE_STEP = 0.5, FADE = 0.5;
// Cluster names are full at the fitted view and gone this many zoom levels in.
const CLUSTER_FADE_START = 0.4, CLUSTER_FADE_LEN = 1.2;

const clamp01 = (v) => Math.max(0, Math.min(1, v));
const truncate = (t) => (t.length > LABEL.chars ? t.slice(0, LABEL.chars - 1).trimEnd() + "…" : t);

/** A CSS colour token as [r, g, b], so the canvas labels follow the theme. */
function tokenRGB(name) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const m = /^#([0-9a-f]{6})$/i.exec(v);
  if (m) return [0, 2, 4].map((i) => parseInt(m[1].slice(i, i + 2), 16));
  const n = /^rgba?\(\s*(\d+)[ ,]+(\d+)[ ,]+(\d+)/i.exec(v);
  return n ? [Number(n[1]), Number(n[2]), Number(n[3])] : [40, 44, 52];
}
const fontFamily = () => getComputedStyle(document.body).fontFamily || "system-ui, sans-serif";

/** One label per cluster, at the paper nearest the cluster's centre so it sits on the cloud. */
function clusterMarks(points, legend) {
  const sums = new Map();
  for (const p of points) {
    const s = sums.get(p.cluster) || { x: 0, y: 0, n: 0 };
    s.x += p.x; s.y += p.y; s.n += 1; sums.set(p.cluster, s);
  }
  const colour = new Map(legend.map((e) => [e.cluster, e.color]));
  return [...sums].map(([cluster, s]) => {
    const cx = s.x / s.n, cy = s.y / s.n;
    let best = null, bd = Infinity;
    for (const p of points) if (p.cluster === cluster) {
      const d = Math.hypot(p.x - cx, p.y - cy);
      if (d < bd) { bd = d; best = p; }
    }
    return { cluster, x: best.x, y: best.y, cx, cy, color: colour.get(cluster) || [128, 128, 128] };
  });
}

/**
 * Decide, per paper, the zoom at which its title appears. Walking up from the
 * fitted view in half-level steps, a paper is labeled when no label handed out
 * so far would overlap it on screen; papers near their cluster's centre go
 * first, so the representative ones surface before the outliers. Returns a
 * Map doi -> zoom (papers that never fit are absent and stay hover-only).
 */
function planTitles(points, marks, fitZoom) {
  const centre = new Map(marks.map((m) => [m.cluster, m]));
  const ranked = points
    .map((p) => { const c = centre.get(p.cluster); return { p, d: c ? Math.hypot(p.x - c.cx, p.y - c.cy) : 0 }; })
    .sort((a, b) => a.d - b.d)
    .map((r) => r.p);
  const at = new Map();
  const labeled = [];
  // Half-label cells and a 5x5 neighbourhood: a free neighbourhood means every
  // earlier label is at least one full label width or height away.
  for (let z = fitZoom + FIRST_TITLES_AT; z <= fitZoom + LAST_TITLES_AT; z += TITLE_STEP) {
    const scale = 2 ** z;
    const cw = LABEL.w / 2 / scale, ch = LABEL.h / 2 / scale;
    const cell = (p) => [Math.floor(p.x / cw), Math.floor(p.y / ch)];
    const taken = new Set();
    for (const p of labeled) { const [i, j] = cell(p); taken.add(i + ":" + j); }
    const free = (p) => {
      const [i, j] = cell(p);
      for (let di = -2; di <= 2; di++) for (let dj = -2; dj <= 2; dj++) if (taken.has((i + di) + ":" + (j + dj))) return false;
      return true;
    };
    for (const p of ranked) {
      if (at.has(p.doi) || !free(p)) continue;
      at.set(p.doi, z);
      labeled.push(p);
      const [i, j] = cell(p); taken.add(i + ":" + j);
    }
  }
  return at;
}

export function libraryMapView() {
  return {
    mount(container) {
      container.innerHTML = `
        <div class="page">
          <h1>Publication Map</h1>
          <p class="lede">UMAP layout of the paper embeddings; KMeans clusters (computed in the full 384-d space)
            labeled with their top title keywords. Scroll to zoom, drag to pan, hover a point for its title —
            a visual answer to “which papers are near the one I'm reading?”</p>
          <div class="map-toolbar">
            <label>Clusters <input type="range" id="clusters" min="2" max="20" value="8"> <b id="clusters-n">8</b></label>
            <label>Find a paper <input type="search" id="paper-search" list="paper-titles" placeholder="Search or pick from the paper list…"><datalist id="paper-titles"></datalist></label>
          </div>
          <div class="legend" id="legend"></div>
          <div class="map-frame"><canvas id="deck-canvas"></canvas><div class="map-status" id="map-status">Loading…</div></div>
        </div>`;
      const $ = (id) => container.querySelector("#" + id);
      const status = $("map-status");
      const wrap = container.querySelector(".map-frame");
      let deckInst = null;
      let data = [];
      let marks = [];
      let titleAt = new Map();
      let fitZoom = 0;
      let zoom = 0;
      let hits = [];
      let disposed = false;

      function frameSize() { return [wrap.clientWidth || 700, wrap.clientHeight || 540]; }

      function extent() {
        const xs = data.map((d) => d.x), ys = data.map((d) => d.y);
        return { minx: Math.min(...xs), maxx: Math.max(...xs), miny: Math.min(...ys), maxy: Math.max(...ys) };
      }

      function baseZoom() {
        const { minx, maxx, miny, maxy } = extent();
        const [W, H] = frameSize();
        return Math.log2(0.85 * Math.min(W / Math.max(maxx - minx, 1e-6), H / Math.max(maxy - miny, 1e-6)));
      }

      function fit(matches) {
        const { minx, maxx, miny, maxy } = extent();
        const [W, H] = frameSize();
        fitZoom = baseZoom();
        // A search rings every matching paper and frames the hits: a single hit gets
        // its ~30-neighbor surroundings; several get a bounding-box fit.
        let target = [(minx + maxx) / 2, (miny + maxy) / 2, 0];
        let z = fitZoom;
        const found = data.filter((d) => matches.has(d.title));
        if (found.length === 1) {
          const sel = found[0];
          const dists = data.map((d) => Math.hypot(d.x - sel.x, d.y - sel.y)).sort((a, b) => a - b);
          const R = dists[Math.min(30, dists.length - 1)] || 1;
          const zin = Math.log2(0.35 * Math.min(W, H) / Math.max(R, 1e-6));
          target = [sel.x, sel.y, 0];
          z = Math.min(Math.max(zin, fitZoom + 1), fitZoom + 5);
        } else if (found.length > 1) {
          const hx = found.map((d) => d.x), hy = found.map((d) => d.y);
          const nx = Math.min(...hx), Xx = Math.max(...hx), ny = Math.min(...hy), Xy = Math.max(...hy);
          target = [(nx + Xx) / 2, (ny + Xy) / 2, 0];
          const zfit = Math.log2(0.8 * Math.min(W / Math.max(Xx - nx, 1e-6), H / Math.max(Xy - ny, 1e-6)));
          z = Math.min(Math.max(zfit, fitZoom), fitZoom + 6);
        }
        // Every title is handed out one level above LAST_TITLES_AT; deeper than
        // that is blank canvas, and further out the map is a speck.
        return { found, viewState: { target, zoom: z, minZoom: fitZoom - 1, maxZoom: fitZoom + LAST_TITLES_AT + 1 } };
      }

      function layers() {
        const { ScatterplotLayer, TextLayer } = window.deck;
        const ink = tokenRGB("--ink"), panel = tokenRGB("--panel"), muted = tokenRGB("--ink-2");
        const font = fontFamily();
        const depth = zoom - fitZoom;
        const clusterAlpha = clamp01(1 - (depth - CLUSTER_FADE_START) / CLUSTER_FADE_LEN);
        // A handful of search hits are always named; beyond that the plan decides.
        const pinned = new Set(hits.length <= 12 ? hits.map((d) => d.doi) : []);
        const titleAlpha = (d) => (pinned.has(d.doi) ? 1 : clamp01((zoom - (titleAt.get(d.doi) ?? Infinity)) / FADE));
        const titled = data.filter((d) => titleAlpha(d) > 0);

        const L = [new ScatterplotLayer({
          id: "points", data,
          getPosition: (d) => [d.x, d.y], getFillColor: (d) => d.color,
          getRadius: 4, radiusUnits: "pixels", radiusMinPixels: 2.5, radiusMaxPixels: 9,
          opacity: 0.85, pickable: true, autoHighlight: true, highlightColor: [255, 255, 255, 140],
        })];
        if (hits.length) L.push(new ScatterplotLayer({
          id: "matched", data: hits, getPosition: (d) => [d.x, d.y],
          filled: false, stroked: true, getLineColor: ink, lineWidthUnits: "pixels",
          getLineWidth: 2, lineWidthMinPixels: 2, lineWidthMaxPixels: 2,
          getRadius: 12, radiusUnits: "pixels", radiusMinPixels: 12, radiusMaxPixels: 12, pickable: false,
          updateTriggers: { getLineColor: ink },
        }));
        if (titled.length) L.push(new TextLayer({
          id: "titles", data: titled, pickable: false, characterSet: "auto", fontFamily: font,
          getPosition: (d) => [d.x, d.y], getText: (d) => truncate(d.title),
          getSize: LABEL.size, sizeUnits: "pixels", getTextAnchor: "start", getAlignmentBaseline: "center",
          getPixelOffset: [LABEL.offset, 0],
          getColor: (d) => [...muted, Math.round(235 * titleAlpha(d))],
          background: true, backgroundPadding: [5, 2],
          getBackgroundColor: (d) => [...panel, Math.round(200 * titleAlpha(d))],
          updateTriggers: { getColor: [zoom, muted], getBackgroundColor: [zoom, panel] },
        }));
        if (clusterAlpha > 0) L.push(new TextLayer({
          id: "cluster-names", data: marks, pickable: false, characterSet: "auto", fontFamily: font, fontWeight: 600,
          getPosition: (d) => [d.x, d.y], getText: (d) => d.cluster,
          getSize: 14, sizeUnits: "pixels", getTextAnchor: "middle", getAlignmentBaseline: "center",
          getColor: [...ink, Math.round(255 * clusterAlpha)],
          background: true, backgroundPadding: [9, 5],
          getBackgroundColor: [...panel, Math.round(225 * clusterAlpha)],
          getBorderColor: (d) => [...d.color, Math.round(255 * clusterAlpha)], getBorderWidth: 1.5,
          updateTriggers: { getBorderColor: clusterAlpha },
        }));
        return L;
      }

      let relayerPending = false;
      function relayer() {
        if (relayerPending || !deckInst) return;
        relayerPending = true;
        requestAnimationFrame(() => { relayerPending = false; if (deckInst && !disposed) deckInst.setProps({ layers: layers() }); });
      }

      function matchesFor(q) {
        q = (q || "").trim();
        if (!q) return new Set();
        const exact = data.find((d) => d.title === q);
        if (exact) return new Set([exact.title]);
        const lq = q.toLowerCase();
        return new Set(data.filter((d) => d.title.toLowerCase().includes(lq)).slice(0, 100).map((d) => d.title));
      }

      function draw() {
        const { found, viewState } = fit(matchesFor($("paper-search").value));
        hits = found;
        zoom = viewState.zoom;
        const { Deck, OrthographicView } = window.deck;
        if (!deckInst) {
          deckInst = new Deck({
            canvas: $("deck-canvas"), views: new OrthographicView({}),
            controller: { scrollZoom: true, dragPan: true, doubleClickZoom: true },
            initialViewState: viewState, layers: layers(),
            onViewStateChange: ({ viewState: vs }) => {
              // Label visibility is a function of zoom; a hundredth of a level is
              // below anything the eye notices and spares a relayer per pan frame.
              const q = Math.round(vs.zoom * 100) / 100;
              if (q !== zoom) { zoom = q; relayer(); }
            },
            getTooltip: ({ object }) => object && { html: `<b>${object.title}</b><br/>${object.year} · ${object.cluster}`, className: "dk-tip" },
          });
        } else {
          deckInst.setProps({ layers: layers(), initialViewState: viewState });
        }
      }

      function renderLegend(legend) {
        const el = $("legend");
        el.innerHTML = '<div class="t">Cluster (top title keywords)</div>' + legend.map((e) =>
          `<span><i style="background:rgb(${e.color.join(",")})"></i>${e.cluster}</span>`).join("");
      }

      async function load(n) {
        status.textContent = cache.has(n) ? "" : "Computing publication map (UMAP + clustering)…";
        status.hidden = cache.has(n);
        try {
          let payload = cache.get(n);
          if (!payload) { payload = await getJSON(`api/publication-map?clusters=${n}`); if (payload.available) cache.set(n, payload); }
          if (disposed) return;
          if (!payload.available) { status.textContent = payload.hint || "Library map not available."; status.hidden = false; return; }
          await loadDeck();
          if (disposed) return;
          data = payload.points;
          if (!$("paper-titles").children.length) {
            const dl = $("paper-titles");
            for (const t of [...new Set(data.map((d) => d.title))].sort()) { const o = document.createElement("option"); o.value = t; dl.append(o); }
          }
          renderLegend(payload.legend);
          marks = clusterMarks(data, payload.legend);
          titleAt = planTitles(data, marks, baseZoom());
          status.hidden = true;
          draw();
        } catch (e) { status.textContent = e.message; status.hidden = false; }
      }

      const slider = $("clusters");
      let timer = 0;
      slider.addEventListener("input", () => {
        $("clusters-n").textContent = slider.value;
        clearTimeout(timer);
        timer = setTimeout(() => load(Number(slider.value)), 250);
      });
      let searchTimer = 0;
      $("paper-search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => data.length && draw(), 200); });
      $("paper-search").addEventListener("change", () => data.length && draw());

      // The fitted zoom depends on the frame, and the label colours on the theme.
      const sizer = new ResizeObserver(() => {
        if (!data.length) return;
        fitZoom = baseZoom();
        titleAt = planTitles(data, marks, fitZoom);
        relayer();
      });
      sizer.observe(wrap);
      const themed = new MutationObserver(relayer);
      themed.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
      const scheme = matchMedia("(prefers-color-scheme: dark)");
      scheme.addEventListener("change", relayer);

      load(8);

      return () => {
        disposed = true;
        sizer.disconnect(); themed.disconnect(); scheme.removeEventListener("change", relayer);
        if (deckInst) { deckInst.finalize(); deckInst = null; }
      };
    },
  };
}
