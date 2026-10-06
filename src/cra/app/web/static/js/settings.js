// Settings row (tools, model and parameters, feedback) and the dialogs (connect, feedback, stats;
// the Settings dialog itself lives in account.js).
import { getJSON, postJSON, del } from "./api.js";
import { initSettings, openSettings } from "./account.js";
import { refreshSession } from "./app.js";
import { escapeHtml } from "./markdown.js";
import { applyTheme, currentTheme, propagateTheme, syncControls } from "./theme.js";
import { toast } from "./toast.js";

let store = null;

export { toast } from "./toast.js";

// ---------- connect dialog ----------
let connectKind = null;

const el = (id) => document.getElementById(id);

function openConnect(kind) {
  connectKind = kind;
  const src = store.config.sources[kind];
  const conn = store.session.connected[kind] || { active: false, tools: 0 };
  el("connect-title").textContent = `Connect ${src.label}`;
  el("connect-label").textContent = src.key_label;
  el("connect-base").value = src.base_url || "";
  el("connect-key").value = "";
  el("connect-token").value = "";
  el("connect-error").hidden = true;
  el("connect-paste").open = false;
  el("connect-disconnect").hidden = !conn.active;
  el("connect-storage").textContent = store.config.sources_kept
    ? "Your key is passed to the registration service to mint a personal token and is not kept. " +
      "The token is stored encrypted with your account, so the connection stays until you disconnect it."
    : "Your key is passed to the registration service to mint a personal token. " +
      "Neither is written to the database; the token lives in this session only.";
  el("connect-hint").textContent = conn.active
    ? "Connected. Registering again replaces the token."
    : "Register your account once; the tools it unlocks are then available in the chat.";
  const field = el("connect-profile-field");
  const select = el("connect-profile");
  field.hidden = !(src.profiles || []).length;
  select.replaceChildren();
  for (const p of src.profiles || []) {
    const o = document.createElement("option");
    o.value = p.value;
    o.textContent = p.label;
    select.append(o);
  }
  el("dlg-connect").showModal();
  el(src.base_url ? "connect-key" : "connect-base").focus();
}

/** Run one connect attempt, keeping the dialog open on a refusal. */
async function attempt(button, label, call) {
  const err = el("connect-error");
  const was = button.textContent;
  button.disabled = true;
  button.textContent = label;
  try {
    const r = await call();
    await refreshSession();
    if (r.active) {
      toast(`${store.config.sources[connectKind].label}: connected`);
      el("dlg-connect").close();
    } else {
      err.textContent = r.error || "That did not work — check the address and the key.";
      err.hidden = false;
    }
  } catch (e) {
    err.textContent = e.message;
    err.hidden = false;
  } finally {
    button.disabled = false;
    button.textContent = was;
  }
}

function submitRegister() {
  const base_url = el("connect-base").value.trim();
  const api_key = el("connect-key").value.trim();
  const err = el("connect-error");
  if (!base_url || !api_key) {
    err.textContent = "The address and the API key are both required.";
    err.hidden = false;
    return;
  }
  const profile = el("connect-profile-field").hidden ? "" : el("connect-profile").value;
  return attempt(el("connect-submit"), "Registering…", () =>
    postJSON(`api/session/register/${connectKind}`, { base_url, api_key, profile }),
  );
}

function submitToken() {
  const token = el("connect-token").value.trim();
  const err = el("connect-error");
  if (!token) {
    err.textContent = "Paste the token first.";
    err.hidden = false;
    return;
  }
  return attempt(el("connect-token-submit"), "Connecting…", () =>
    postJSON(`api/session/connect/${connectKind}`, { token }),
  );
}

async function disconnect() {
  try {
    await del(`api/session/connect/${connectKind}`);
    await refreshSession();
    toast(`${store.config.sources[connectKind].label} disconnected`);
    el("dlg-connect").close();
  } catch (e) {
    toast(e.message, "bad");
  }
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
  body.querySelector("[data-key]")?.focus();
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
  if (!text) {
    err.textContent = "Add a note before submitting.";
    err.hidden = false;
    return;
  }
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
  } catch (e) {
    err.textContent = e.message;
    err.hidden = false;
  }
}

// ---------- stats dialog ----------
export async function openStats() {
  const body = document.getElementById("stats-body");
  body.innerHTML = '<p class="muted">Loading…</p>';
  document.getElementById("dlg-stats").showModal();
  try {
    const s = await getJSON("api/stats");
    body.innerHTML = renderStats(s);
  } catch (e) {
    body.innerHTML = `<p class="error">${escapeHtml(e.message)}</p>`;
  }
}

function renderStats(s) {
  const built = s.build.library_built_at
    ? s.build.library_built_at.slice(0, 16).replace("T", " ")
    : "unknown";
  const u = s.usage;
  const tools = store.session.tools;
  const inventory = [`${tools.local} local`].concat(sourceCounts(store, tools)).join(" · ");
  const kpi = (n, l) =>
    `<div class="kpi"><div class="n">${escapeHtml(n)}</div><div class="l">${escapeHtml(l)}</div></div>`;
  const row = (cells, num = []) =>
    `<tr>${cells.map((c, i) => `<td class="${num.includes(i) ? "num" : ""}">${escapeHtml(c ?? "—")}</td>`).join("")}</tr>`;
  return `
    <p class="muted small">Version <code>${escapeHtml(s.build.version)}</code> · provider ${escapeHtml(s.provider)} ·
      default model <code>${escapeHtml(s.default_model)}</code> · tools in this session: ${escapeHtml(inventory)}</p>
    <section><h3>Usage (every stored answer)</h3>
      <div class="kpis">${kpi(u.turns, "answers")}${kpi(u.conversations, "conversations")}${kpi(u.people, "people")}${kpi(u.error_turns, "failed answers")}${kpi(u.avg_latency_ms + " ms", "⌀ answer time")}${kpi(u.feedback, "feedback")}</div>
    </section>
    <section><h3>Library (built ${escapeHtml(built)} UTC${s.build.embedding_model ? `, embeddings by ${escapeHtml(s.build.embedding_model)}` : ""})</h3>
      <table><thead><tr><th>stage</th><th class="num">entries</th><th>available</th></tr></thead><tbody>
      ${s.pipeline.map((p) => row([p.stage, p.entries == null ? "—" : String(p.entries), p.available ? "yes" : "no"], [1])).join("")}
      </tbody></table></section>
    <section><h3>Tools</h3>
      <table><thead><tr><th>tool</th><th class="num">calls</th><th class="num">errors</th><th class="num">avg</th></tr></thead><tbody>
      ${s.tools.length ? s.tools.map((t) => row([t.name, String(t.calls), String(t.errors), t.avg_ms + " ms"], [1, 2, 3])).join("") : row(["none yet", "", "", ""])}
      </tbody></table></section>
    <section><h3>Models</h3>
      <table><thead><tr><th>model</th><th class="num">answers</th></tr></thead><tbody>
      ${s.models.length ? s.models.map((m) => row([m.name, String(m.turns)], [1])).join("") : row(["none yet", ""])}
      </tbody></table></section>
    `;
}

/** Native dialogs only close on Escape; make the ✕ and the backdrop dismiss
 *  them the same way. The mousedown check keeps a selection drag that ends on
 *  the backdrop from closing the dialog. */
function dismissable(dlg) {
  let fromBackdrop = false;
  dlg.addEventListener("mousedown", (e) => {
    fromBackdrop = e.target === dlg;
  });
  dlg.addEventListener("click", (e) => {
    if ((fromBackdrop && e.target === dlg) || e.target.closest("[data-close]")) dlg.close("cancel");
  });
}

// :open is recent; where it is unknown an Enter that picks an option must not apply
function listIsOpen(select) {
  try {
    return select.matches(":open");
  } catch {
    return true;
  }
}

/** Enter applies the dialog. The primary button is the form's only submit
 *  button, so Enter in a text field reaches it by itself; a closed select does
 *  not submit on its own, and a textarea needs ⌘/Ctrl since Enter is a newline
 *  there. Without an action Enter does nothing rather than close the dialog. */
function applyOnEnter(dlg, action) {
  const form = dlg.querySelector("form");
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    action?.();
  });
  if (!action) return;
  dlg.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || e.isComposing || e.defaultPrevented) return;
    const t = e.target;
    const applies = t.matches("select")
      ? !listIsOpen(t)
      : t.matches("textarea") && (e.metaKey || e.ctrlKey);
    if (!applies) return;
    e.preventDefault();
    form.requestSubmit();
  });
}

export function initDialogs(s) {
  store = s;
  const actions = {
    "dlg-connect": submitRegister,
    "dlg-params": applyParams,
    "dlg-feedback": submitFeedback,
  };
  for (const dlg of document.querySelectorAll("dialog.dlg")) {
    dismissable(dlg);
    applyOnEnter(dlg, actions[dlg.id]);
  }
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
  document.getElementById("stats-btn").addEventListener("click", () => {
    menu.open = false;
    openStats();
  });
  initSettings(s);
  document.getElementById("settings-btn").addEventListener("click", () => {
    menu.open = false;
    openSettings();
  });
  // absent when the deployment describes no pipeline
  const box = document.getElementById("pipeline-box");
  box?.addEventListener("toggle", () => {
    const f = document.getElementById("pipeline-frame");
    if (box.open && !f.src) {
      f.src = f.dataset.src;
      f.addEventListener("load", () => propagateTheme(f), { once: true });
    }
  });
  document.getElementById("connect-token-submit").addEventListener("click", submitToken);
  document.getElementById("connect-disconnect").addEventListener("click", disconnect);
  document.getElementById("connect-token").addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      submitToken();
    }
  });
  document.getElementById("params-reset").addEventListener("click", resetParams);
}

// ---------- settings row ----------
function svgIcon(id) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "ico");
  svg.innerHTML = `<use href="#${id}"/>`;
  return svg;
}

function chip(icon, text, title) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "chip";
  b.title = title;
  b.append(svgIcon(icon), text);
  return b;
}

/** A chip that opens a menu above it; the menu is filled by the caller. */
function picker(icon, label, title) {
  const d = document.createElement("details");
  d.className = "picker";
  const summary = document.createElement("summary");
  summary.className = "chip";
  summary.title = title;
  const text = document.createElement("span");
  text.className = "chip-text";
  text.textContent = label;
  summary.append(svgIcon(icon), text, svgIcon("i-chevron"));
  summary.lastChild.classList.add("chev");
  const menu = document.createElement("div");
  menu.className = "picker-menu";
  d.append(summary, menu);
  return { picker: d, summary, menu };
}

function heading(text) {
  const h = document.createElement("h4");
  h.textContent = text;
  return h;
}

function hint(text) {
  const p = document.createElement("div");
  p.className = "hint";
  p.textContent = text;
  return p;
}

export function renderSettingsRow(store, el) {
  const cfg = store.config;
  el.replaceChildren();
  // Each part appears only once the server offers what it needs, so the row
  // still carries the feedback button while the rest is being built.
  if (Object.keys(cfg.sources || {}).length) el.append(toolsPicker(store));
  if (store.session.model || store.session.auto_model) el.append(modelPicker(store));

  const spacer = document.createElement("span");
  spacer.className = "spacer";
  el.append(spacer);
  const fb = chip("i-feedback", "Feedback", "Report a bug or leave a note about an answer");
  fb.addEventListener("click", openFeedback);
  el.append(fb);
}

function toolsPicker(store) {
  const connected = store.session.connected || {};
  const sources = Object.entries(store.config.sources);
  const active = sources.filter(([kind]) => connected[kind]?.active).map(([, src]) => src.label);
  const {
    picker: p,
    summary,
    menu,
  } = picker(
    "i-tools",
    "Tools",
    active.length
      ? `Connected: ${active.join(", ")}`
      : "The library is built in; connect further sources here",
  );
  summary.classList.toggle("on", active.length > 0);
  menu.classList.add("tool-menu");

  menu.append(heading("Built in"));
  const library = document.createElement("div");
  library.className = "tool-row fixed";
  library.innerHTML = `<span class="dot on"></span><span class="tool-name">${escapeHtml(store.config.cluster?.name || "Cluster")} library</span><span class="tool-state">always on</span>`;
  menu.append(library);

  menu.append(heading("Connectors"));
  for (const [kind, src] of sources) {
    const on = Boolean(connected[kind]?.active);
    const b = document.createElement("button");
    b.type = "button";
    b.className = "tool-row";
    b.title = on
      ? "Connected — click to replace the token or disconnect"
      : "Not connected — click to register and paste a token";
    b.innerHTML =
      `<span class="dot${on ? " on" : ""}"></span><span class="tool-name">${escapeHtml(src.label)}</span>` +
      `<span class="tool-state">${on ? "connected" : "connect…"}</span>`;
    b.addEventListener("click", () => {
      p.open = false;
      openConnect(kind);
    });
    menu.append(b);
  }
  menu.append(hint("The assistant calls these when a question needs them."));
  return p;
}

function modelPicker(store) {
  const { session, config } = store;
  const changed = changedParams(store);
  const label = session.auto_model ? `auto · ${session.route_label}` : session.model;
  const {
    picker: p,
    summary,
    menu,
  } = picker(
    "i-model",
    label,
    changed.length
      ? `Choose the model · parameters changed: ${changed.join(", ")}`
      : "Choose the model and its parameters",
  );
  summary.classList.toggle("on", changed.length > 0);
  if (changed.length) {
    const mark = document.createElement("span");
    mark.className = "tuned";
    mark.title = "Parameters changed for this session";
    summary.insertBefore(mark, summary.querySelector(".chev"));
  }

  const pick = async (body) => {
    try {
      await postJSON("api/session/model", body);
      await refreshSession();
    } catch (e) {
      toast(e.message, "bad");
    }
    p.open = false;
  };

  const models = config.openrouter ? ["", ...(config.models || [])] : config.models || [];
  menu.append(heading(config.provider || "model"));
  if (!models.length) {
    const current = document.createElement("div");
    current.className = "current-model";
    current.textContent = session.model;
    menu.append(current);
  }
  // on OpenRouter an empty pick means "choose for me", so it leads the list
  for (const model of models) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = model || "auto (cheapest that can call tools)";
    const picked = model ? model === session.model && !session.auto_model : session.auto_model;
    b.className = picked ? "on" : "";
    b.addEventListener("click", () => pick({ model }));
    menu.append(b);
  }

  if (config.openrouter) {
    menu.append(heading("routing"));
    for (const route of config.routes) {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = route.label;
      b.className = route.value === session.sort ? "on" : "";
      b.addEventListener("click", () => pick({ model: session.model, sort: route.value }));
      menu.append(b);
    }
  }

  menu.append(document.createElement("hr"));
  const params = document.createElement("button");
  params.type = "button";
  params.className = "action";
  params.append(svgIcon("i-sliders"), "Parameters…");
  params.addEventListener("click", () => {
    p.open = false;
    openParams();
  });
  menu.append(
    params,
    hint(
      changed.length
        ? `Changed for this session: ${changed.join(", ")}`
        : "Reasoning, sampling and the tool-call limit",
    ),
  );
  return p;
}

function sourceCounts(store, tools) {
  return Object.entries(store.config.sources || {})
    .filter(([kind]) => tools[kind])
    .map(([kind, src]) => `${tools[kind]} ${src.label}`);
}
