// Settings row (sources, model, feedback) and the dialogs (connect, feedback, stats).
import { getJSON, postJSON, del } from "./api.js";
import { refreshSession } from "./app.js";
import { escapeHtml } from "./markdown.js";
import { applyTheme, currentTheme, propagateTheme, syncControls } from "./theme.js";

let store = null;

export function toast(message, kind = "") {
  const t = document.createElement("div");
  t.className = "toast " + kind;
  t.textContent = message;
  document.getElementById("toasts").append(t);
  setTimeout(() => t.remove(), 3200);
}

// ---------- connect dialog ----------
let connectKind = null;

function openConnect(kind) {
  connectKind = kind;
  const src = store.config.sources[kind];
  const conn = store.session.connected[kind];
  document.getElementById("connect-title").textContent = `Connect ${src.label}`;
  document.getElementById("connect-label").textContent = `${src.label} token`;
  // Mirrored through the app: the upstream page refuses to be framed cross-origin.
  document.getElementById("connect-frame").src = `api/register/${kind}`;
  document.getElementById("connect-link").href = src.register_url;
  document.getElementById("connect-token").value = "";
  document.getElementById("connect-error").hidden = true;
  document.getElementById("connect-disconnect").hidden = !conn.active;
  document.getElementById("connect-hint").textContent = conn.active
    ? `Connected — ${conn.tools} tools available. Paste a new token to replace it.`
    : "Register below — you stay inside the app. Then paste the token.";
  document.getElementById("dlg-connect").showModal();
}

async function submitConnect() {
  const token = document.getElementById("connect-token").value.trim();
  const err = document.getElementById("connect-error");
  const btn = document.getElementById("connect-submit");
  if (!token) { err.textContent = "Paste the token first."; err.hidden = false; return; }
  btn.disabled = true; btn.textContent = "Connecting…";
  try {
    const r = await postJSON(`api/session/connect/${connectKind}`, { token });
    await refreshSession();
    if (r.active) {
      toast(`${store.config.sources[connectKind].label}: connected (${r.tools} tools)`);
      document.getElementById("dlg-connect").close();
    } else {
      err.textContent = r.error || "Token invalid or expired — register a new one above.";
      err.hidden = false;
    }
  } catch (e) { err.textContent = e.message; err.hidden = false; }
  finally { btn.disabled = false; btn.textContent = "Connect"; }
}

async function disconnect() {
  try {
    await del(`api/session/connect/${connectKind}`);
    await refreshSession();
    toast(`${store.config.sources[connectKind].label} disconnected`);
    document.getElementById("dlg-connect").close();
  } catch (e) { toast(e.message, "bad"); }
}

// ---------- parameters dialog ----------
const optionLabel = (field, value) => field.option_labels?.[value] ?? (value || "default");

/** The fields this session set away from what the deployment configures. */
function changedParams(store) {
  const { defaults } = store.config.parameters;
  const effective = store.session.params || {};
  return Object.keys(defaults).filter((k) => effective[k] !== defaults[k]);
}

function paramField(field, effective, defaults) {
  const wrap = document.createElement("div");
  const label = document.createElement("label");
  label.className = "field";
  const name = document.createElement("span");
  name.textContent = field.label;
  if (effective[field.key] !== defaults[field.key]) {
    const changed = document.createElement("b");
    changed.className = "muted small";
    changed.textContent = " \u00b7 changed";
    name.append(changed);
  }
  label.append(name);

  if (field.type === "number") {
    const input = document.createElement("input");
    input.type = "number";
    input.dataset.key = field.key;
    input.min = String(field.min);
    input.max = String(field.max);
    input.step = String(field.step || 1);
    input.placeholder = defaults[field.key] || "default";
    input.value = effective[field.key] || "";
    label.append(input);
  } else {
    const select = document.createElement("select");
    select.dataset.key = field.key;
    for (const option of field.options) {
      const o = document.createElement("option");
      o.value = option;
      o.textContent = optionLabel(field, option);
      o.selected = option === effective[field.key];
      select.append(o);
    }
    label.append(select);
  }
  wrap.append(label);
  if (field.help) {
    const help = document.createElement("p");
    help.className = "hint";
    help.textContent = field.help;
    wrap.append(help);
  }
  return wrap;
}

function openParams() {
  const { spec, defaults } = store.config.parameters;
  const effective = store.session.params || {};
  const body = document.getElementById("params-body");
  document.getElementById("params-error").hidden = true;
  body.replaceChildren(
    ...spec.filter((f) => !f.hidden).map((f) => paramField(f, effective, defaults)),
  );
  document.getElementById("dlg-params").showModal();
}

function readParams() {
  const values = {};
  for (const el of document.querySelectorAll("#params-body [data-key]")) {
    values[el.dataset.key] = el.value;
  }
  return values;
}

async function applyParams() {
  const err = document.getElementById("params-error");
  try {
    await postJSON("api/session/params", { params: readParams() });
    await refreshSession();
    document.getElementById("dlg-params").close();
    toast("Parameters applied");
  } catch (e) {
    err.textContent = e.message;
    err.hidden = false;
  }
}

async function resetParams() {
  try {
    await del("api/session/params");
    await refreshSession();
    document.getElementById("dlg-params").close();
    toast("Parameters reset");
  } catch (e) {
    toast(e.message, "bad");
  }
}

// ---------- feedback dialog ----------
function openFeedback() {
  document.getElementById("feedback-text").value = "";
  document.getElementById("feedback-error").hidden = true;
  document.getElementById("dlg-feedback").showModal();
  document.getElementById("feedback-text").focus();
}

async function submitFeedback() {
  const text = document.getElementById("feedback-text").value.trim();
  const category = document.querySelector("#feedback-category input:checked").value;
  const err = document.getElementById("feedback-error");
  if (!text) { err.textContent = "Add a note before submitting."; err.hidden = false; return; }
  try {
    await postJSON("api/feedback", {
      category,
      text,
      model: store.session?.model || "",
      messages: (store.session?.messages || []).map((m) => ({
        role: m.role,
        content: m.content,
      })),
    });
    document.getElementById("dlg-feedback").close();
    toast("Thanks — recorded.");
  } catch (e) { err.textContent = e.message; err.hidden = false; }
}

// ---------- stats dialog ----------
export async function openStats() {
  const body = document.getElementById("stats-body");
  body.innerHTML = '<p class="muted">Loading…</p>';
  document.getElementById("dlg-stats").showModal();
  try {
    const s = await getJSON("api/stats");
    body.innerHTML = renderStats(s);
  } catch (e) { body.innerHTML = `<p class="error">${escapeHtml(e.message)}</p>`; }
}

function renderStats(s) {
  const build = s.build.git_sha + (s.build.build_time ? ` (${s.build.build_time})` : "");
  const u = s.usage;
  const tools = store.session.tools;
  const inventory = [`${tools.local} local`]
    .concat(tools.elab ? [`${tools.elab} eLabFTW`] : [], tools.dt ? [`${tools.dt} DataTagger`] : []).join(" · ");
  const kpi = (n, l) => `<div class="kpi"><div class="n">${escapeHtml(n)}</div><div class="l">${escapeHtml(l)}</div></div>`;
  const row = (cells, num = []) => `<tr>${cells.map((c, i) => `<td class="${num.includes(i) ? "num" : ""}">${escapeHtml(c ?? "—")}</td>`).join("")}</tr>`;
  return `
    <p class="muted small">Build <code>${escapeHtml(build)}</code> · providers: ${escapeHtml(s.providers.join(", "))} ·
      default model <code>${escapeHtml(s.default_model)}</code> · tools in this session: ${escapeHtml(inventory)}</p>
    <section><h3>Usage (from the server logs)</h3>
      <div class="kpis">${kpi(u.turns, "turns")}${kpi(u.sessions, "sessions")}${kpi(u.error_turns, "error turns")}${kpi(u.avg_latency_ms + " ms", "⌀ latency")}${kpi(u.feedback, "feedback")}</div>
    </section>
    <section><h3>Pipeline (sources → caches → tools)</h3>
      <table><thead><tr><th>stage</th><th class="num">entries</th><th>available</th><th>built</th></tr></thead><tbody>
      ${s.pipeline.map((p) => row([p.stage, p.entries == null ? (p.available ? "yes" : "no") : String(p.entries), p.available ? "yes" : "no", p.built], [1])).join("")}
      </tbody></table></section>
    <section><h3>Tools</h3>
      <table><thead><tr><th>tool</th><th class="num">calls</th><th class="num">errors</th><th class="num">avg</th></tr></thead><tbody>
      ${s.tools.length ? s.tools.map((t) => row([t.name, String(t.calls), String(t.errors), t.avg_ms + " ms"], [1, 2, 3])).join("") : row(["none yet", "", "", ""])}
      </tbody></table></section>
    <section><h3>Models</h3>
      <table><thead><tr><th>model</th><th class="num">turns</th></tr></thead><tbody>
      ${s.models.length ? s.models.map((m) => row([m.name, String(m.turns)], [1])).join("") : row(["none yet", ""])}
      </tbody></table></section>
    `;
}

/** Native dialogs only close on Escape or the ✕; make the backdrop dismiss them
 *  too. The mousedown check keeps a selection drag that ends on the backdrop
 *  from closing the dialog. */
function closeOnBackdropClick(dlg) {
  let fromBackdrop = false;
  dlg.addEventListener("mousedown", (e) => { fromBackdrop = e.target === dlg; });
  dlg.addEventListener("click", (e) => { if (fromBackdrop && e.target === dlg) dlg.close("cancel"); });
}

export function initDialogs(s) {
  store = s;
  for (const dlg of document.querySelectorAll("dialog.dlg")) closeOnBackdropClick(dlg);
  syncControls(currentTheme());
  document.getElementById("theme-seg").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-theme]");
    if (b) applyTheme(b.dataset.theme);
  });
  const menu = document.getElementById("menu");
  // Pop-up <details> dismiss like the dialogs do. Expanders that are part of a
  // panel (#pipeline-box) are deliberately not listed.
  const popovers = () => document.querySelectorAll("details.menu[open], details.picker[open]");
  document.addEventListener("click", (e) => {
    for (const d of popovers()) if (!d.contains(e.target)) d.open = false;
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") for (const d of popovers()) d.open = false;
  });
  document.getElementById("stats-btn").addEventListener("click", () => { menu.open = false; openStats(); });
  const box = document.getElementById("pipeline-box");
  box.addEventListener("toggle", () => {
    const f = document.getElementById("pipeline-frame");
    if (box.open && !f.src) { f.src = f.dataset.src; f.addEventListener("load", () => propagateTheme(f), { once: true }); }
  });
  document.getElementById("connect-submit").addEventListener("click", submitConnect);
  document.getElementById("connect-disconnect").addEventListener("click", disconnect);
  document.getElementById("connect-token").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); submitConnect(); } });
  document.getElementById("feedback-submit").addEventListener("click", submitFeedback);
  document.getElementById("params-apply").addEventListener("click", applyParams);
  document.getElementById("params-reset").addEventListener("click", resetParams);
  document.getElementById("dlg-connect").addEventListener("close", () => { document.getElementById("connect-frame").src = "about:blank"; });
}

// ---------- settings row ----------
export function renderSettingsRow(store, el) {
  const session = store.session;
  const cfg = store.config;
  el.replaceChildren();

  // Each part appears only once the server offers what it needs, so the row
  // still carries the feedback button while the rest is being built.
  for (const [kind, src] of Object.entries(cfg.sources || {})) {
    const conn = (session.connected || {})[kind] || { active: false, tools: 0 };
    const b = document.createElement("button");
    b.type = "button";
    b.className = "chip" + (conn.active ? " on" : "");
    b.title = conn.active ? `connected — ${conn.tools} tools available` : "not connected — click to register and paste a token";
    b.innerHTML = `<span class="dot"></span>${escapeHtml(src.label)}${conn.active ? ` <span class="muted">· ${conn.tools}</span>` : ""}`;
    b.addEventListener("click", () => openConnect(kind));
    el.append(b);
  }

  if ((cfg.models || []).length || cfg.openrouter) el.append(modelPicker(store));

  const changed = changedParams(store);
  const params = document.createElement("button");
  params.type = "button";
  params.className = "chip" + (changed.length ? " on" : "");
  params.textContent = changed.length ? `Parameters \u00b7 ${changed.length}` : "Parameters";
  params.title = changed.length
    ? `Changed for this session: ${changed.join(", ")}`
    : "Reasoning, sampling and the tool-call limit";
  params.addEventListener("click", openParams);
  el.append(params);

  const spacer = document.createElement("span"); spacer.className = "spacer"; el.append(spacer);
  const fb = document.createElement("button");
  fb.type = "button"; fb.className = "chip"; fb.textContent = "Feedback";
  fb.title = "Report a bug or leave a note about an answer";
  fb.addEventListener("click", openFeedback);
  el.append(fb);
}

function modelPicker(store) {
  const { session, config } = store;
  const picker = document.createElement("details");
  picker.className = "picker";
  const label = session.auto_model ? `auto · ${session.route_label}` : session.model;
  picker.innerHTML =
    `<summary class="chip" title="Choose the model">${escapeHtml(label)} \u25be</summary>` +
    `<div class="picker-menu"></div>`;
  const menu = picker.querySelector(".picker-menu");

  const pick = async (body) => {
    try {
      await postJSON("api/session/model", body);
      await refreshSession();
    } catch (e) {
      toast(e.message, "bad");
    }
    picker.open = false;
  };

  const heading = document.createElement("h4");
  heading.textContent = config.provider || "models";
  menu.append(heading);

  // on OpenRouter an empty pick means "choose for me", so it leads the list
  for (const model of config.openrouter ? ["", ...config.models] : config.models) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = model || "auto (cheapest that can call tools)";
    const picked = model ? model === session.model && !session.auto_model : session.auto_model;
    b.className = picked ? "on" : "";
    b.addEventListener("click", () => pick({ model }));
    menu.append(b);
  }

  if (config.openrouter) {
    const routing = document.createElement("h4");
    routing.textContent = "routing";
    menu.append(routing);
    for (const route of config.routes) {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = route.label;
      b.className = route.value === session.sort ? "on" : "";
      b.addEventListener("click", () => pick({ model: session.model, sort: route.value }));
      menu.append(b);
    }
  }

  const tools = session.tools || { local: 0 };
  const inventory = document.createElement("div");
  inventory.className = "hint";
  inventory.textContent =
    "Tools: " +
    [`${tools.local} local`]
      .concat(
        tools.elab ? [`${tools.elab} eLabFTW`] : [],
        tools.dt ? [`${tools.dt} DataTagger`] : [],
      )
      .join(" \u00b7 ");
  menu.append(inventory);
  return picker;
}
