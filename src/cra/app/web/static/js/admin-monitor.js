// Server info and Logs tabs of the admin console. Both poll only while their
// tab is open and the page is visible, and back off when the server fails.
import { getJSON } from "./api.js";

const POLL_MS = 5000;
const MAX_BACKOFF_MS = 60000;
const LOG_PAGE = 100;

const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};
const svg = (tag, attrs = {}) => {
  const n = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  return n;
};

const bytes = (n) => {
  if (n == null) return "—";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(n >= 100 || i === 0 ? 0 : 1)} ${units[i]}`;
};
const count = (n) => (n == null ? "—" : Intl.NumberFormat(undefined, { notation: n >= 1e5 ? "compact" : "standard" }).format(n));
const dollars = (n) => (n == null ? "—" : `$${Number(n).toFixed(2)}`);
const duration = (s) => {
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d} d ${h} h` : h ? `${h} h ${m} min` : `${m} min`;
};
const ago = (iso) => {
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${Math.max(0, s)} s ago`;
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(iso).toLocaleString();
};

/** Polls ``load`` while running; a failure keeps the last values on screen. */
function poller(load, onStale) {
  let timer = null, delay = POLL_MS, running = false;
  const tick = async () => {
    timer = null;
    if (!running) return;
    if (document.hidden) { timer = setTimeout(tick, POLL_MS); return; }
    try {
      await load();
      delay = POLL_MS;
      onStale(null);
    } catch (e) {
      delay = Math.min(delay * 2, MAX_BACKOFF_MS);
      onStale(e);
    }
    if (running) timer = setTimeout(tick, delay);
  };
  const wake = () => { if (running && !document.hidden && timer) { clearTimeout(timer); tick(); } };
  return {
    start() { if (running) return; running = true; addEventListener("visibilitychange", wake); tick(); },
    stop() { running = false; removeEventListener("visibilitychange", wake); if (timer) clearTimeout(timer); timer = null; },
  };
}

function staleBanner(box) {
  let since = null;
  return (error) => {
    if (!error) { since = null; box.hidden = true; return; }
    since = since || new Date();
    box.hidden = false;
    box.textContent = `These values stopped updating at ${since.toLocaleTimeString()} (${error.message}). Retrying.`;
  };
}

// ---------- pieces ----------

const tile = (value, label, status) => {
  const t = el("div", "kpi");
  const n = el("div", "n", value);
  t.append(n, el("div", "l", label));
  if (status) {
    t.classList.add(`status-${status.kind}`);
    t.append(el("div", "status-note", `${status.icon} ${status.text}`));
  }
  return t;
};

function meter(label, used, total, detail) {
  const row = el("div", "meter");
  const head = el("div", "meter-head");
  head.append(el("span", null, label), el("span", "muted", detail ?? (total ? `${bytes(used)} of ${bytes(total)}` : bytes(used))));
  const bar = el("div", "meter-bar");
  const fill = el("div", "meter-fill");
  const share = total ? Math.min(1, used / total) : 0;
  fill.style.width = `${(share * 100).toFixed(1)}%`;
  if (share >= 0.9) fill.classList.add("bad");
  else if (share >= 0.75) fill.classList.add("warn");
  bar.append(fill);
  bar.setAttribute("role", "meter");
  bar.setAttribute("aria-valuemin", "0");
  bar.setAttribute("aria-valuemax", String(total || 0));
  bar.setAttribute("aria-valuenow", String(used || 0));
  bar.setAttribute("aria-label", label);
  row.append(head, bar);
  return row;
}

function facts(pairs) {
  const dl = el("dl", "facts");
  for (const [k, v] of pairs) dl.append(el("dt", null, k), el("dd", null, v ?? "—"));
  return dl;
}

function emptyRow(text, span) {
  const tr = el("tr", "empty");
  const td = el("td", "desc", text);
  td.colSpan = span;
  tr.append(td);
  return tr;
}

function section(title, ...children) {
  const s = el("section", "monitor-block");
  s.append(el("h2", null, title), ...children);
  return s;
}

/** One series per hour as bars; a hover names the hour and the value. */
function hourlyBars(hours, value, title, format) {
  const W = 480, H = 120, top = 14, bottom = 18;
  const values = hours.map(value);
  const max = Math.max(1, ...values);
  const plot = H - top - bottom;
  const step = W / hours.length;
  const figure = el("figure", "chart");
  figure.append(el("figcaption", null, title));
  const chart = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `${title}, last 24 hours` });
  chart.append(svg("line", { x1: 0, x2: W, y1: H - bottom, y2: H - bottom, class: "axis" }));
  const maxLabel = svg("text", { x: 0, y: 10, class: "tick" });
  maxLabel.textContent = format(max);
  chart.append(maxLabel);
  const tip = el("div", "chart-tip");
  tip.hidden = true;
  hours.forEach((h, i) => {
    const v = values[i];
    const height = (v / max) * plot;
    const x = i * step + 1;
    const w = Math.max(1, step - 2);
    if (v > 0) {
      // 4px rounded data end, square at the baseline
      const r = Math.min(4, w / 2, height);
      const y = H - bottom - height;
      chart.append(svg("path", {
        class: "bar",
        d: `M${x},${H - bottom}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${H - bottom}Z`,
      }));
    }
    const hour = new Date(h.hour);
    if (i % 6 === 0) {
      const t = svg("text", { x: x, y: H - 4, class: "tick" });
      t.textContent = `${String(hour.getHours()).padStart(2, "0")}:00`;
      chart.append(t);
    }
    const hit = svg("rect", { x: i * step, y: 0, width: step, height: H, class: "hit" });
    hit.addEventListener("pointerenter", () => {
      tip.hidden = false;
      tip.textContent = `${hour.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}: ${format(v)}`;
      tip.style.left = `${((i + 0.5) / hours.length) * 100}%`;
    });
    hit.addEventListener("pointerleave", () => { tip.hidden = true; });
    chart.append(hit);
  });
  figure.append(chart, tip);
  return figure;
}

function hourlyTable(hours) {
  const details = el("details", "chart-table");
  details.append(el("summary", "desc", "Show as table"));
  const table = el("table");
  table.innerHTML = "<thead><tr><th>Hour</th><th class='num'>Answers</th><th class='num'>Failed</th><th class='num'>Prompt tokens</th><th class='num'>Completion tokens</th></tr></thead>";
  const body = el("tbody");
  for (const h of hours.filter((x) => x.answers)) {
    const tr = el("tr");
    tr.append(el("td", null, new Date(h.hour).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" })));
    for (const k of ["answers", "failed", "prompt", "completion"]) tr.append(el("td", "num", count(h[k])));
    body.append(tr);
  }
  if (!body.children.length) body.append(emptyRow("No answers in the last 24 hours.", 5));
  table.append(body);
  details.append(table);
  return details;
}

// ---------- Server info ----------

function creditStatus(credit) {
  if (!credit || credit.limit == null || credit.limit_remaining == null) return null;
  const share = credit.limit_remaining / credit.limit;
  if (share < 0.1) return { kind: "bad", icon: "⛔", text: "nearly used up" };
  if (share < 0.25) return { kind: "warn", icon: "⚠", text: "running low" };
  return { kind: "ok", icon: "✓", text: "enough" };
}

function renderServer(box, s) {
  const { system: sys, app, database: db, llm, usage } = s;
  const t = usage.totals;
  const credit = llm.credit;
  const budget = el("div", "kpis");
  if (credit) {
    budget.append(
      tile(dollars(credit.limit_remaining), "credit left", creditStatus(credit)),
      tile(dollars(credit.usage_daily), "spent today (UTC)"),
      tile(dollars(credit.usage), "spent in all"),
      tile(credit.limit == null ? "none" : dollars(credit.limit), "key limit"),
    );
  } else {
    budget.append(tile("unknown", "credit left (OpenRouter keys only)"));
  }
  budget.append(tile(count(t.prompt + t.completion), "tokens, 24 h"), tile(count(t.avg_tokens_per_answer), "⌀ tokens per answer"));

  const activity = el("div", "kpis");
  activity.append(
    tile(count(app.active_users["5m"]), "active, 5 min"),
    tile(count(app.active_users["1h"]), "active, 1 h"),
    tile(count(app.active_users["24h"]), "active, 24 h"),
    tile(count(app.turns_running), "answers being written"),
    tile(count(t.answers), "answers, 24 h"),
    tile(count(t.failed), "failed, 24 h", t.failed ? { kind: "warn", icon: "⚠", text: "see below" } : null),
  );

  const charts = el("div", "charts");
  charts.append(
    hourlyBars(usage.hours, (h) => h.answers, "Answers per hour", count),
    hourlyBars(usage.hours, (h) => h.prompt + h.completion, "Tokens per hour", count),
  );

  const failures = Object.entries(usage.failures);
  const failureList = failures.length
    ? facts(failures.map(([kind, n]) => [kind.replaceAll("_", " "), String(n)]))
    : el("p", "desc", "No failed answers in the last 24 hours.");

  const memory = el("div", "meters");
  const rss = sys.process.rss ?? sys.process.peak_rss;
  if (sys.container) {
    memory.append(meter("Container memory", sys.container.used, sys.container.limit,
      sys.container.limit ? null : `${bytes(sys.container.used)}, no limit`));
  }
  memory.append(meter(sys.process.rss != null ? "This process" : "This process (peak)", rss, sys.container?.limit ?? sys.host?.total));
  if (sys.host) {
    memory.append(meter("Host memory", sys.host.total - sys.host.available, sys.host.total));
    if (sys.host.swap_total) memory.append(meter("Swap", sys.host.swap_total - sys.host.swap_free, sys.host.swap_total));
  }
  for (const d of sys.disks) memory.append(meter(`Disk (${d.name})`, d.total - d.free, d.total));

  const sources = Object.entries(app.source_connections);
  box.replaceChildren(
    section("Model budget", budget),
    section("Activity", activity, charts, hourlyTable(usage.hours)),
    section("Failed answers, 24 h", failureList),
    section("Memory and disk", memory),
    section("System", facts([
      ["Host", sys.hostname],
      ["Platform", sys.platform],
      ["CPUs", String(sys.cpus ?? "—")],
      ["Load (1, 5, 15 min)", sys.load ? sys.load.map((x) => x.toFixed(2)).join(", ") : "—"],
      ["This process, CPU", sys.process_cpu_percent == null ? "measuring…" : `${sys.process_cpu_percent} % of one core`],
      ["Up for", duration(sys.uptime_s)],
      ["Python", sys.python],
      ["Version", app.version],
    ])),
    section("Database", facts([
      ["Type", `${db.dialect} ${db.version ?? ""}`],
      ["Size", bytes(db.size)],
      ["Connections (server)", db.connections == null ? "—" : String(db.connections)],
      ["Pool", db.pool ? `${db.pool.checked_out} in use, ${db.pool.size} kept open` + (db.pool.overflow ? `, ${db.pool.overflow} extra` : "") : "—"],
      ...Object.entries(db.rows).map(([k, v]) => [k[0].toUpperCase() + k.slice(1), count(v)]),
    ])),
    section("Models and sources", facts([
      ["Provider", llm.provider],
      ["Default model", llm.default_model],
      ["Open lab connections", sources.length ? sources.map(([k, n]) => `${k}: ${n}`).join(", ") : "none"],
      ["Background tasks", String(app.background_tasks)],
    ])),
  );
}

export function serverTab(root) {
  const stale = el("p", "stale");
  stale.hidden = true;
  const updated = el("p", "desc");
  const box = el("div");
  root.replaceChildren(updated, stale, box);
  return poller(async () => {
    const s = await getJSON("api/admin/server");
    renderServer(box, s);
    updated.textContent = `Updated ${new Date(s.time).toLocaleTimeString()}; refreshes every ${POLL_MS / 1000} s while this tab is open.`;
  }, staleBanner(stale));
}

// ---------- Logs ----------

function logRow(entry) {
  const { ts, level, logger, msg, exc, ...fields } = entry;
  const row = el("tr", "log-row");
  const badge = el("span", `level level-${String(level).toLowerCase()}`, level);
  const when = el("td", "desc nowrap", ago(ts));
  when.title = new Date(ts).toLocaleString();
  const text = el("td");
  text.append(el("div", null, msg));
  const keys = Object.keys(fields);
  if (keys.length) {
    const summary = keys.map((k) => `${k}=${JSON.stringify(fields[k])}`).join("  ");
    text.append(el("div", "src", summary.length > 160 ? summary.slice(0, 160) + " …" : summary));
  }
  const levelCell = el("td");
  levelCell.append(badge);
  row.append(levelCell, when, el("td", "desc", String(logger || "").replace(/^cra\./, "")), text);
  const detail = el("tr", "log-detail");
  detail.hidden = true;
  const cell = el("td");
  cell.colSpan = 4;
  const pre = el("pre", null, JSON.stringify({ ts, level, logger, msg, ...fields }, null, 2) + (exc ? `\n\n${exc}` : ""));
  cell.append(pre);
  detail.append(cell);
  row.addEventListener("click", () => { detail.hidden = !detail.hidden; row.classList.toggle("open", !detail.hidden); });
  return [row, detail];
}

export function logsTab(root) {
  const controls = el("div", "row-form wrap log-controls");
  const levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"];
  const chosen = new Set(["INFO", "WARNING", "ERROR", "CRITICAL"]);
  for (const level of levels) {
    const label = el("label", "check");
    const box = el("input");
    box.type = "checkbox";
    box.checked = chosen.has(level);
    box.addEventListener("change", () => { box.checked ? chosen.add(level) : chosen.delete(level); reload(); });
    label.append(box, document.createTextNode(` ${level.toLowerCase()}`));
    controls.append(label);
  }
  const search = el("input");
  search.type = "search";
  search.placeholder = "Search messages and fields";
  let debounce = null;
  search.addEventListener("input", () => { clearTimeout(debounce); debounce = setTimeout(reload, 300); });
  const followLabel = el("label", "check");
  const follow = el("input");
  follow.type = "checkbox";
  follow.checked = true;
  followLabel.append(follow, document.createTextNode(" follow"));
  const download = el("a", "btn ghost", "Download");
  download.href = "api/admin/logs/download";
  download.setAttribute("download", "");
  controls.append(search, followLabel, download);

  const stale = el("p", "stale");
  stale.hidden = true;
  const table = el("table", "logs");
  table.innerHTML = "<thead><tr><th>Level</th><th>When</th><th>Source</th><th>Message</th></tr></thead>";
  const body = el("tbody");
  table.append(body);
  const more = el("button", "btn ghost", "Load older entries");
  more.type = "button";
  more.hidden = true;
  root.replaceChildren(controls, stale, table, more);

  let newest = null, next = null, generation = 0;
  const params = (extra) => {
    const q = new URLSearchParams({ levels: [...chosen].join(","), q: search.value.trim(), limit: String(LOG_PAGE), ...extra });
    return `api/admin/logs?${q}`;
  };
  async function reload() {
    const mine = ++generation;
    const page = await getJSON(params({}));
    if (mine !== generation) return;
    body.replaceChildren();
    if (!page.entries.length) body.append(emptyRow("No entries match.", 4));
    for (const e of page.entries) body.append(...logRow(e));
    newest = page.entries[0]?.ts ?? newest;
    next = page.next;
    more.hidden = !next;
  }
  more.addEventListener("click", async () => {
    const page = await getJSON(params({ before: next.before, skip: String(next.skip) }));
    for (const e of page.entries) body.append(...logRow(e));
    next = page.next;
    more.hidden = !next;
  });
  let started = false;
  const live = poller(async () => {
    if (!started) { started = true; await reload(); return; }
    if (!follow.checked || !newest) return;
    const page = await getJSON(params({ after: newest }));
    if (!page.entries.length) return;
    body.querySelector("tr.empty")?.remove();
    const rows = page.entries.flatMap(logRow);
    body.prepend(...rows);
    newest = page.entries[0].ts;
  }, staleBanner(stale));
  return { start: () => live.start(), stop: () => live.stop() };
}
