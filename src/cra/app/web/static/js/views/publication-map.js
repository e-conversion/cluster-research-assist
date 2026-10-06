// Library map: UMAP layout of the paper embeddings rendered with deck.gl.
import { getJSON, postJSON } from "../api.js";
import { escapeHtml } from "../markdown.js";
import { toast } from "../toast.js";
import { dismissOnEscape, expandable, loadScript } from "./viz.js";

const DECK_URL = "https://cdn.jsdelivr.net/npm/deck.gl@9.0.38/dist.min.js";
// pinned build; bump both together
const DECK_INTEGRITY = "sha384-tEG529toczQRv/bqd2PyHy5vTO49FMjQri0c1oB44gSkYiNouZz8L9yNTVx0YGl+";
const loadDeck = () => loadScript(DECK_URL, DECK_INTEGRITY, "deck");

const cache = new Map(); // clusters -> payload (server memoises too; this saves the round-trip)

// Title labels: footprint of one label on screen, and how far above the
// fitted view the first titles appear and the last ones are handed out.
const LABEL = { w: 345, h: 22, size: 11.5, chars: 60, offset: 9 };
const FIRST_TITLES_AT = 0.75,
  LAST_TITLES_AT = 8,
  TITLE_STEP = 0.5,
  FADE = 0.5;
// Cluster names are full at the fitted view and gone this many zoom levels in.
const CLUSTER_FADE_START = 0.4,
  CLUSTER_FADE_LEN = 1.2;
// A cluster name's box: the padding around its text, and the step in zoom at
// which a name that would cover another one is tried again.
const NAME = { padX: 9, padY: 5, step: 0.25 };
// After a release the view keeps moving this long, as a touch screen leads one to expect.
const INERTIA_MS = 300;
const GLIDE_MS = 450;

const clamp01 = (v) => Math.max(0, Math.min(1, v));
const truncate = (t) => (t.length > LABEL.chars ? t.slice(0, LABEL.chars - 1).trimEnd() + "…" : t);
/** "an e-conversion paper", "a cluster paper": the cluster names itself. */
const article = (word) => (/^[aeiou]/i.test(word) ? "an" : "a");

/** A DOI as the library stores it, from whatever the user pasted. */
const bareDoi = (raw) =>
  raw
    .trim()
    .toLowerCase()
    .replace(/^https?:\/\/(dx\.)?doi\.org\//, "")
    .replace(/^doi:/, "")
    .replace(/[}\s]+$/, "");

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
    s.x += p.x;
    s.y += p.y;
    s.n += 1;
    sums.set(p.cluster, s);
  }
  const colour = new Map(legend.map((e) => [e.cluster, e.color]));
  return [...sums].map(([cluster, s]) => {
    const cx = s.x / s.n,
      cy = s.y / s.n;
    let best = null,
      bd = Infinity;
    for (const p of points)
      if (p.cluster === cluster) {
        const d = Math.hypot(p.x - cx, p.y - cy);
        if (d < bd) {
          bd = d;
          best = p;
        }
      }
    return {
      cluster,
      n: s.n,
      x: best.x,
      y: best.y,
      cx,
      cy,
      color: colour.get(cluster) || [128, 128, 128],
    };
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
    .map((p) => {
      const c = centre.get(p.cluster);
      return { p, d: c ? Math.hypot(p.x - c.cx, p.y - c.cy) : 0 };
    })
    .sort((a, b) => a.d - b.d)
    .map((r) => r.p);
  const at = new Map();
  const labeled = [];
  // Half-label cells and a 5x5 neighbourhood: a free neighbourhood means every
  // earlier label is at least one full label width or height away.
  for (let z = fitZoom + FIRST_TITLES_AT; z <= fitZoom + LAST_TITLES_AT; z += TITLE_STEP) {
    const scale = 2 ** z;
    const cw = LABEL.w / 2 / scale,
      ch = LABEL.h / 2 / scale;
    const cell = (p) => [Math.floor(p.x / cw), Math.floor(p.y / ch)];
    const taken = new Set();
    for (const p of labeled) {
      const [i, j] = cell(p);
      taken.add(i + ":" + j);
    }
    const free = (p) => {
      const [i, j] = cell(p);
      for (let di = -2; di <= 2; di++)
        for (let dj = -2; dj <= 2; dj++) if (taken.has(i + di + ":" + (j + dj))) return false;
      return true;
    };
    for (const p of ranked) {
      if (at.has(p.doi) || !free(p)) continue;
      at.set(p.doi, z);
      labeled.push(p);
      const [i, j] = cell(p);
      taken.add(i + ":" + j);
    }
  }
  return at;
}

/**
 * Decide, per cluster, where and from which zoom its name shows. Biggest
 * cluster first, a name goes on its cloud, or one label height above or below
 * it, wherever its box clears every name placed before it for as long as names
 * show at all. One that fits nowhere waits for the zoom at which it does: on a
 * phone the clouds sit too close for every name at the fitted view, and the
 * legend lists them all. Returns a Map cluster -> { z, dy } (dy in pixels);
 * names that never fit are absent.
 */
function planNames(marks, fitZoom, size) {
  const measure = document.createElement("canvas").getContext("2d");
  measure.font = `600 ${size}px ${fontFamily()}`;
  const boxes = [...marks]
    .sort((a, b) => b.n - a.n)
    .map((m) => ({
      m,
      w: measure.measureText(m.cluster).width + 2 * NAME.padX + 4,
      h: size + 2 * NAME.padY + 4,
    }));
  const zooms = [];
  for (let z = fitZoom; z <= fitZoom + CLUSTER_FADE_START + CLUSTER_FADE_LEN; z += NAME.step)
    zooms.push(z);
  // Shifted boxes can close in on each other as the clouds spread apart, so a
  // place must stay clear at every later zoom, not only the first.
  const clear = (b, dy, from) =>
    shown.every((o) =>
      zooms.slice(from).every((z) => {
        const k = 2 ** z;
        return (
          Math.abs((b.m.x - o.m.x) * k) >= (b.w + o.w) / 2 ||
          Math.abs((b.m.y - o.m.y) * k + dy - o.dy) >= (b.h + o.h) / 2
        );
      }),
    );
  const at = new Map();
  const shown = [];
  zooms.forEach((z, i) => {
    for (const b of boxes) {
      if (at.has(b.m.cluster)) continue;
      const dy = [0, -b.h, b.h].find((shift) => clear(b, shift, i));
      if (dy === undefined) continue;
      at.set(b.m.cluster, { z, dy });
      shown.push({ ...b, dy });
    }
  });
  return at;
}

export function libraryMapView(store) {
  return {
    mount(container) {
      // The cluster names itself, so this reads "an e-conversion paper" where
      // it is deployed and stays sensible anywhere else.
      const ours = store?.config?.cluster?.name || "cluster";
      container.innerHTML = `
        <div class="page">
          <h1>Publication Map</h1>
          <p class="lede">UMAP layout of the paper embeddings; KMeans clusters (computed in the full 384-d space)
            labeled with their top title keywords. Hover over a point for its citation; click or tap it for the details.</p>
          <div class="map-row">
            <p class="row-hint">Adjust number of clusters</p>
            <div class="map-toolbar">
              <label>Clusters <input type="range" id="clusters" min="2" max="20" value="8"> <b id="clusters-n">8</b></label>
            </div>
          </div>
          <div class="map-row">
            <p class="row-hint">Locate ${escapeHtml(article(ours))} ${escapeHtml(ours)} publication.</p>
            <div class="map-toolbar">
              <label>Find ${escapeHtml(article(ours))} ${escapeHtml(ours)} paper <input type="search" id="paper-search" list="paper-titles" enterkeyhint="search" autocapitalize="none" autocorrect="off" spellcheck="false" placeholder="Title, author, year, journal or DOI…"><datalist id="paper-titles"></datalist></label>
            </div>
          </div>
          <div class="map-row">
            <p class="row-hint">Locate an external work based on the DOI.</p>
            <div class="map-toolbar">
              <label>Place a DOI <input type="search" id="doi-input" autocomplete="off" inputmode="url" enterkeyhint="go" autocapitalize="none" autocorrect="off" spellcheck="false" placeholder="10.1038/s41586-021-03819-2, or a doi.org link…"></label>
              <button class="btn primary" id="doi-go" type="button">Place on the map</button>
            </div>
          </div>
          <div class="lookup-card" id="lookup-card" hidden></div>
          <div class="legend" id="legend"></div>
          <div class="viz-frame map-frame"><canvas id="deck-canvas"></canvas><div class="map-status" id="map-status">Loading…</div>
            <div class="map-card" id="map-card" hidden></div></div>
        </div>`;
      const $ = (id) => container.querySelector("#" + id);
      const status = $("map-status");
      const wrap = container.querySelector(".map-frame");
      const mapCard = $("map-card");
      let deckInst = null;
      let data = [];
      let marks = [];
      let titleAt = new Map();
      let nameAt = new Map();
      let fitZoom = 0;
      let zoom = 0;
      let hits = [];
      let placed = null; // the response for a DOI brought in from outside
      let selected = null; // the point whose details the card shows
      let busy = false;
      let disposed = false;
      // A touch screen has no hover: a tap pins the card instead, and a
      // tooltip left behind where the finger lifted would only repeat it.
      const noHover = matchMedia("(hover: none)");
      const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)");

      function frameSize() {
        return [wrap.clientWidth || 700, wrap.clientHeight || 540];
      }
      // smaller names on a phone, so more of them fit beside each other
      const nameSize = () => (frameSize()[0] < 520 ? 12.5 : 14);

      function extent() {
        const xs = data.map((d) => d.x),
          ys = data.map((d) => d.y);
        return {
          minx: Math.min(...xs),
          maxx: Math.max(...xs),
          miny: Math.min(...ys),
          maxy: Math.max(...ys),
        };
      }

      function baseZoom() {
        const { minx, maxx, miny, maxy } = extent();
        const [W, H] = frameSize();
        return Math.log2(
          0.85 * Math.min(W / Math.max(maxx - minx, 1e-6), H / Math.max(maxy - miny, 1e-6)),
        );
      }

      /** Everything that depends on the frame's size: the fitted zoom and the label plans. */
      function plan() {
        fitZoom = baseZoom();
        titleAt = planTitles(data, marks, fitZoom);
        nameAt = planNames(marks, fitZoom, nameSize());
      }

      function fit(matches) {
        const { minx, maxx, miny, maxy } = extent();
        const [W, H] = frameSize();
        fitZoom = baseZoom();
        // A search rings every matching paper and frames the hits: a single hit gets
        // its ~30-neighbor surroundings; several get a bounding-box fit.
        let target = [(minx + maxx) / 2, (miny + maxy) / 2, 0];
        let z = fitZoom;
        const found = data.filter((d) => matches.has(d.doi));
        if (placed && !found.length) {
          // Frame the new paper together with the papers it was placed from,
          // which is the whole point of having looked it up.
          const near = [placed.point, ...placed.neighbours];
          const nx = Math.min(...near.map((d) => d.x)),
            Xx = Math.max(...near.map((d) => d.x));
          const ny = Math.min(...near.map((d) => d.y)),
            Xy = Math.max(...near.map((d) => d.y));
          target = [(nx + Xx) / 2, (ny + Xy) / 2, 0];
          const zfit = Math.log2(
            0.55 * Math.min(W / Math.max(Xx - nx, 1e-6), H / Math.max(Xy - ny, 1e-6)),
          );
          z = Math.min(Math.max(zfit, fitZoom + 0.5), fitZoom + 5);
        } else if (found.length === 1) {
          const sel = found[0];
          const dists = data.map((d) => Math.hypot(d.x - sel.x, d.y - sel.y)).sort((a, b) => a - b);
          const R = dists[Math.min(30, dists.length - 1)] || 1;
          const zin = Math.log2((0.35 * Math.min(W, H)) / Math.max(R, 1e-6));
          target = [sel.x, sel.y, 0];
          z = Math.min(Math.max(zin, fitZoom + 1), fitZoom + 5);
        } else if (found.length > 1) {
          const hx = found.map((d) => d.x),
            hy = found.map((d) => d.y);
          const nx = Math.min(...hx),
            Xx = Math.max(...hx),
            ny = Math.min(...hy),
            Xy = Math.max(...hy);
          target = [(nx + Xx) / 2, (ny + Xy) / 2, 0];
          const zfit = Math.log2(
            0.8 * Math.min(W / Math.max(Xx - nx, 1e-6), H / Math.max(Xy - ny, 1e-6)),
          );
          z = Math.min(Math.max(zfit, fitZoom), fitZoom + 6);
        }
        // Every title is handed out one level above LAST_TITLES_AT; deeper than
        // that is blank canvas, and further out the map is a speck.
        return {
          found,
          viewState: {
            target,
            zoom: z,
            minZoom: fitZoom - 1,
            maxZoom: fitZoom + LAST_TITLES_AT + 1,
          },
        };
      }

      function layers() {
        const { ScatterplotLayer, TextLayer } = window.deck;
        const ink = tokenRGB("--ink"),
          panel = tokenRGB("--panel"),
          muted = tokenRGB("--ink-2");
        const font = fontFamily();
        const depth = zoom - fitZoom;
        const clusterAlpha = clamp01(1 - (depth - CLUSTER_FADE_START) / CLUSTER_FADE_LEN);
        // full at the zoom the plan gave it, faded in over the step before
        const nameAlpha = (d) =>
          Math.min(
            clusterAlpha,
            clamp01(1 + (zoom - (nameAt.get(d.cluster)?.z ?? Infinity)) / NAME.step),
          );
        const named = marks.filter((d) => nameAlpha(d) > 0);
        // A handful of search hits are always named; beyond that the plan decides.
        const pinned = new Set(hits.length <= 12 ? hits.map((d) => d.doi) : []);
        const titleAlpha = (d) =>
          pinned.has(d.doi) ? 1 : clamp01((zoom - (titleAt.get(d.doi) ?? Infinity)) / FADE);
        const titled = data.filter((d) => titleAlpha(d) > 0);

        const L = [
          new ScatterplotLayer({
            id: "points",
            data,
            getPosition: (d) => [d.x, d.y],
            getFillColor: (d) => d.color,
            getRadius: 4,
            radiusUnits: "pixels",
            radiusMinPixels: 2.5,
            radiusMaxPixels: 9,
            opacity: 0.85,
            pickable: true,
            autoHighlight: true,
            highlightColor: [255, 255, 255, 140],
          }),
        ];
        if (hits.length)
          L.push(
            new ScatterplotLayer({
              id: "matched",
              data: hits,
              getPosition: (d) => [d.x, d.y],
              filled: false,
              stroked: true,
              getLineColor: ink,
              lineWidthUnits: "pixels",
              getLineWidth: 2,
              lineWidthMinPixels: 2,
              lineWidthMaxPixels: 2,
              getRadius: 12,
              radiusUnits: "pixels",
              radiusMinPixels: 12,
              radiusMaxPixels: 12,
              pickable: false,
              updateTriggers: { getLineColor: ink },
            }),
          );
        if (selected) {
          const accent = tokenRGB("--accent");
          L.push(
            new ScatterplotLayer({
              id: "selected",
              data: [selected],
              getPosition: (d) => [d.x, d.y],
              filled: false,
              stroked: true,
              getLineColor: accent,
              lineWidthUnits: "pixels",
              getLineWidth: 2.5,
              getRadius: 10,
              radiusUnits: "pixels",
              radiusMinPixels: 10,
              radiusMaxPixels: 10,
              pickable: false,
              updateTriggers: { getLineColor: accent },
            }),
          );
        }
        if (titled.length)
          L.push(
            new TextLayer({
              id: "titles",
              data: titled,
              pickable: false,
              characterSet: "auto",
              fontFamily: font,
              getPosition: (d) => [d.x, d.y],
              getText: (d) => truncate(d.cite || d.title),
              getSize: LABEL.size,
              sizeUnits: "pixels",
              getTextAnchor: "start",
              getAlignmentBaseline: "center",
              getPixelOffset: [LABEL.offset, 0],
              getColor: (d) => [...muted, Math.round(235 * titleAlpha(d))],
              background: true,
              backgroundPadding: [5, 2],
              getBackgroundColor: (d) => [...panel, Math.round(200 * titleAlpha(d))],
              updateTriggers: { getColor: [zoom, muted], getBackgroundColor: [zoom, panel] },
            }),
          );
        if (named.length)
          L.push(
            new TextLayer({
              id: "cluster-names",
              data: named,
              pickable: false,
              characterSet: "auto",
              fontFamily: font,
              fontWeight: 600,
              getPosition: (d) => [d.x, d.y],
              getText: (d) => d.cluster,
              getSize: nameSize(),
              sizeUnits: "pixels",
              getTextAnchor: "middle",
              getAlignmentBaseline: "center",
              getPixelOffset: (d) => [0, nameAt.get(d.cluster).dy],
              getColor: (d) => [...ink, Math.round(255 * nameAlpha(d))],
              background: true,
              backgroundPadding: [NAME.padX, NAME.padY],
              getBackgroundColor: (d) => [...panel, Math.round(225 * nameAlpha(d))],
              getBorderColor: (d) => [...d.color, Math.round(255 * nameAlpha(d))],
              getBorderWidth: 1.5,
              updateTriggers: {
                getColor: [zoom, ink],
                getBackgroundColor: [zoom, panel],
                getBorderColor: zoom,
                getPixelOffset: nameAt,
              },
            }),
          );
        if (placed) {
          const { LineLayer } = window.deck;
          const accent = tokenRGB("--accent");
          // Lines to the papers the position was averaged from: the estimate
          // is an interpolation, and drawing it says so.
          L.push(
            new LineLayer({
              id: "placed-links",
              data: placed.neighbours.slice(0, 5),
              getSourcePosition: () => [placed.point.x, placed.point.y],
              getTargetPosition: (d) => [d.x, d.y],
              getColor: [...accent, 120],
              getWidth: 1,
              widthUnits: "pixels",
              pickable: false,
              updateTriggers: { getColor: accent, getSourcePosition: placed.point },
            }),
          );
          L.push(
            new ScatterplotLayer({
              id: "placed",
              data: [placed.point],
              getPosition: (d) => [d.x, d.y],
              getFillColor: [...accent, 235],
              getRadius: 8,
              radiusUnits: "pixels",
              radiusMinPixels: 8,
              radiusMaxPixels: 8,
              stroked: true,
              getLineColor: ink,
              lineWidthUnits: "pixels",
              getLineWidth: 2,
              pickable: true,
              updateTriggers: { getFillColor: accent, getLineColor: ink },
            }),
          );
          L.push(
            new TextLayer({
              id: "placed-label",
              data: [placed.point],
              pickable: false,
              characterSet: "auto",
              fontFamily: font,
              getPosition: (d) => [d.x, d.y],
              getText: (d) => truncate(d.cite),
              getSize: LABEL.size + 0.5,
              sizeUnits: "pixels",
              fontWeight: 600,
              getTextAnchor: "start",
              getAlignmentBaseline: "center",
              getPixelOffset: [LABEL.offset + 4, 0],
              getColor: [...ink, 255],
              background: true,
              backgroundPadding: [6, 3],
              getBackgroundColor: [...panel, 240],
              getBorderColor: [...accent, 255],
              getBorderWidth: 1.5,
              updateTriggers: { getColor: ink, getBackgroundColor: panel, getBorderColor: accent },
            }),
          );
        }
        return L;
      }

      let relayerPending = false;
      function relayer() {
        if (relayerPending || !deckInst) return;
        relayerPending = true;
        requestAnimationFrame(() => {
          relayerPending = false;
          if (deckInst && !disposed) deckInst.setProps({ layers: layers() });
        });
      }

      // A DOI is the join key everywhere: two papers can share a title, and
      // the citation the datalist offers is not the title either.
      function matchesFor(q) {
        q = (q || "").trim();
        if (!q) return new Set();
        const asDoi = bareDoi(q);
        const exact = data.find((d) => d.cite === q || d.title === q || d.doi === asDoi);
        if (exact) return new Set([exact.doi]);
        // Every term must appear, so a second word narrows the result.
        const terms = q.toLowerCase().split(/\s+/).filter(Boolean);
        const found = new Set();
        for (const d of data) {
          if (terms.every((term) => d._hay.includes(term))) {
            found.add(d.doi);
            if (found.size >= 100) break;
          }
        }
        return found;
      }

      function draw() {
        const { found, viewState } = fit(matchesFor($("paper-search").value));
        hits = found;
        const { Deck, OrthographicView, LinearInterpolator } = window.deck;
        if (!deckInst) {
          zoom = viewState.zoom;
          deckInst = new Deck({
            canvas: $("deck-canvas"),
            views: new OrthographicView({}),
            controller: {
              scrollZoom: true,
              dragPan: true,
              doubleClickZoom: true,
              touchZoom: true,
              inertia: INERTIA_MS,
            },
            initialViewState: viewState,
            layers: layers(),
            onViewStateChange: ({ viewState: vs }) => {
              // Label visibility is a function of zoom; a hundredth of a level is
              // below anything the eye notices and spares a relayer per pan frame.
              const q = Math.round(vs.zoom * 100) / 100;
              if (q !== zoom) {
                zoom = q;
                relayer();
              }
            },
            onClick: ({ object }) => select(object || null),
            getTooltip: ({ object }) =>
              object &&
              object !== selected &&
              !noHover.matches && {
                html: object.placed
                  ? `<b>${escapeHtml(object.cite)}</b><br/>not in the library · placed among its nearest neighbours`
                  : `<b>${escapeHtml(object.cite || object.title)}</b><br/>${escapeHtml(object.title)}<br/>${escapeHtml(String(object.year || ""))} · ${escapeHtml(String(object.cluster || ""))}`,
                className: "dk-tip",
              },
          });
        } else {
          // glide to a search hit or a placed DOI rather than jump there
          const glide = reduceMotion.matches
            ? {}
            : {
                transitionDuration: GLIDE_MS,
                transitionInterpolator: new LinearInterpolator(["target", "zoom"]),
              };
          deckInst.setProps({ layers: layers(), initialViewState: { ...viewState, ...glide } });
        }
      }

      /** Pin a point's details in the card, or clear them with null. */
      function select(object) {
        selected = object;
        mapCard.hidden = !object;
        if (object) {
          const meta = object.placed
            ? "Not in the library · placed among its nearest neighbours"
            : [object.year, object.cluster].filter(Boolean).join(" · ");
          const doi = String(object.doi || "");
          const title =
            object.title && object.title !== object.cite && object.title !== doi
              ? `<div class="title">${escapeHtml(object.title)}</div>`
              : "";
          const link = doi.startsWith("10.")
            ? `<a href="https://doi.org/${escapeHtml(encodeURI(doi))}" target="_blank" rel="noopener noreferrer">doi.org/${escapeHtml(doi)}</a>`
            : "";
          mapCard.innerHTML = `<button type="button" class="close" aria-label="Close">✕</button>
            <div class="cite">${escapeHtml(object.cite || object.title)}</div>${title}
            <div class="meta">${escapeHtml(String(meta))}</div>${link}`;
          mapCard.querySelector(".close").addEventListener("click", () => select(null));
        }
        relayer();
      }

      function renderLegend(legend) {
        const el = $("legend");
        el.innerHTML =
          '<div class="t">Cluster (top title keywords)</div>' +
          legend
            .map(
              (e) =>
                `<span><i style="background:rgb(${e.color.join(",")})"></i>${e.cluster}</span>`,
            )
            .join("");
      }

      async function load(n) {
        status.textContent = cache.has(n) ? "" : "Computing publication map (UMAP + clustering)…";
        status.hidden = cache.has(n);
        try {
          let payload = cache.get(n);
          if (!payload) {
            payload = await getJSON(`api/publication-map?clusters=${n}`);
            if (payload.available) cache.set(n, payload);
          }
          if (disposed) return;
          if (!payload.available) {
            status.textContent = payload.hint || "Library map not available.";
            status.hidden = false;
            return;
          }
          await loadDeck();
          // the cluster names are measured in the page's font
          await document.fonts?.ready;
          if (disposed) return;
          data = payload.points;
          // One haystack per paper, built once: doing it per keystroke would
          // rebuild every string on a library of a few thousand.
          for (const d of data) {
            d._hay =
              `${d.cite || ""} ${d.title || ""} ${d.au || ""} ${d.year || ""} ${d.doi}`.toLowerCase();
          }
          if (!$("paper-titles").children.length) {
            const dl = $("paper-titles");
            for (const c of [...new Set(data.map((d) => d.cite || d.title))].sort()) {
              const o = document.createElement("option");
              o.value = c;
              dl.append(o);
            }
          }
          renderLegend(payload.legend);
          marks = clusterMarks(data, payload.legend);
          plan();
          // a new clustering renames the clusters; the card follows its paper
          if (selected && !selected.placed)
            select(data.find((d) => d.doi === selected.doi) || null);
          status.hidden = true;
          draw();
        } catch (e) {
          status.textContent = e.message;
          status.hidden = false;
        }
      }

      const card = $("lookup-card");

      function showCard(html, bad = false) {
        card.className = "lookup-card" + (bad ? " bad" : "");
        card.innerHTML = html;
        card.hidden = false;
      }

      function describe(found) {
        const point = found.point;
        const lines = [`<div class="cite">${escapeHtml(point.cite)}</div>`];
        if (found.in_library) {
          lines.push(
            '<div class="meta">Already in the library — shown at its own place on the map.</div>',
          );
        } else {
          const percent = Math.round((found.confidence || 0) * 100);
          const where = found.duplicate_of
            ? "The library already holds what looks like the same paper"
            : `Placed among the ${found.used} closest papers, which the projection was not refitted for`;
          lines.push(
            `<div class="meta">${where}. Closest match ${percent}% similar. Metadata from ${escapeHtml(found.source)}.</div>`,
          );
          if (!found.has_abstract) {
            lines.push(
              '<div class="meta">No abstract was published for this DOI, so it was placed by its title alone — treat the position as rough.</div>',
            );
          }
        }
        if (found.neighbours.length) {
          const items = found.neighbours
            .slice(0, 5)
            .map((n) => `<li>${escapeHtml(n.cite)}</li>`)
            .join("");
          lines.push(`<div class="meta">Closest in the library:</div><ol>${items}</ol>`);
        }
        return lines.join("");
      }

      function clearLookup() {
        placed = null;
        card.hidden = true;
        if (selected?.placed) select(null);
        if (data.length) draw();
      }

      async function lookupDoi() {
        const raw = $("doi-input").value.trim();
        if (!raw || busy) return;
        busy = true;
        const button = $("doi-go");
        button.disabled = true;
        button.textContent = "Looking up…";
        // the on-screen keyboard would cover the map the paper lands on
        if (noHover.matches) $("doi-input").blur();
        try {
          const found = await postJSON("api/publication-map/lookup", { doi: raw });
          if (disposed) return;
          placed = found;
          showCard(describe(found));
          draw();
        } catch (e) {
          if (disposed) return;
          // The toast goes; the card stays while the DOI is being corrected.
          toast(e.message, "bad");
          placed = null;
          showCard(escapeHtml(e.message), true);
          if (data.length) draw();
        } finally {
          busy = false;
          button.disabled = false;
          button.textContent = "Place on the map";
        }
      }

      $("doi-go").addEventListener("click", lookupDoi);
      $("doi-input").addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          lookupDoi();
        }
      });
      // Emptying the field undoes the placement, the way emptying the search
      // field undoes a search. That covers the input's own clear button, a
      // selection deleted by hand, and the Escape key alike.
      $("doi-input").addEventListener("input", () => {
        // An error card belongs to the DOI that caused it, so it goes too.
        if (!$("doi-input").value.trim() && (placed || !card.hidden)) clearLookup();
      });

      const slider = $("clusters");
      let timer = 0;
      slider.addEventListener("input", () => {
        $("clusters-n").textContent = slider.value;
        clearTimeout(timer);
        timer = setTimeout(() => load(Number(slider.value)), 250);
      });
      let searchTimer = 0;
      $("paper-search").addEventListener("input", () => {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(() => data.length && draw(), 200);
      });
      $("paper-search").addEventListener("change", () => data.length && draw());
      // the search key on an on-screen keyboard closes it, so the hits are in view
      $("paper-search").addEventListener("keydown", (e) => {
        if (e.key === "Enter" && noHover.matches) e.target.blur();
      });

      // The fitted zoom depends on the frame, and the label colours on the theme.
      const sizer = new ResizeObserver(() => {
        if (!data.length) return;
        plan();
        relayer();
      });
      sizer.observe(wrap);
      const unexpand = expandable(wrap);
      const offEscape = dismissOnEscape(() => {
        if (!selected) return false;
        select(null);
        return true;
      });
      const themed = new MutationObserver(relayer);
      themed.observe(document.documentElement, {
        attributes: true,
        attributeFilter: ["data-theme"],
      });
      const scheme = matchMedia("(prefers-color-scheme: dark)");
      scheme.addEventListener("change", relayer);

      load(8);

      return () => {
        disposed = true;
        unexpand();
        offEscape();
        sizer.disconnect();
        themed.disconnect();
        scheme.removeEventListener("change", relayer);
        if (deckInst) {
          deckInst.finalize();
          deckInst = null;
        }
      };
    },
  };
}
