// The Settings dialog: appearance, MCP tokens and the account itself.
// Token labels are user input, so every row is built from elements and text,
// never from an HTML string.
import { getJSON, postJSON, del } from "./api.js";
import { syncControls } from "./theme.js";
import { toast } from "./toast.js";

const CONFIRM_WORD = "delete";
const el = (id) => document.getElementById(id);
let store = null;

// the server stores naive UTC timestamps
const parseUtc = (iso) => new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : iso + "Z");
const day = (iso) => (iso ? parseUtc(iso).toLocaleDateString(undefined, { dateStyle: "medium" }) : "—");
const moment = (iso) => (iso ? parseUtc(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "never");

function showError(id, message) {
  const p = el(id);
  p.textContent = message || "";
  p.hidden = !message;
}

// ---------- MCP tokens ----------

function mcpUrl() {
  return new URL(store.config.mcp.path, document.baseURI).href;
}

/** A name for the server entry in a client's configuration. */
function serverName() {
  return (store.config.cluster.name || "cluster").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "cluster";
}

function tokenRow(token) {
  const tr = document.createElement("tr");
  if (token.state !== "active") tr.className = "ended";
  const cells = [token.label, day(token.created_at), day(token.expires_at), moment(token.last_used_at)];
  for (const text of cells) {
    const td = document.createElement("td");
    td.textContent = text;
    tr.append(td);
  }
  const action = document.createElement("td");
  if (token.state === "active") {
    const revoke = document.createElement("button");
    revoke.type = "button";
    revoke.className = "btn danger ghost";
    revoke.textContent = "Revoke";
    revoke.addEventListener("click", () => revokeToken(token, revoke));
    action.append(revoke);
  } else {
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = token.state;
    action.append(badge);
  }
  tr.append(action);
  return tr;
}

async function loadTokens() {
  try {
    const { tokens } = await getJSON("api/tokens");
    el("token-rows").replaceChildren(...tokens.map(tokenRow));
    el("token-table").hidden = !tokens.length;
    el("token-empty").hidden = tokens.length > 0;
  } catch (e) {
    showError("token-error", e.message);
  }
}

async function revokeToken(token, button) {
  button.disabled = true;
  try {
    await del(`api/tokens/${encodeURIComponent(token.id)}`);
    toast(`Revoked “${token.label}”`);
    await loadTokens();
  } catch (e) {
    button.disabled = false;
    toast(e.message, "bad");
  }
}

async function createToken() {
  const label = el("token-label").value.trim();
  if (!label) { showError("token-error", "Give the token a label, so you know later which client holds it."); return; }
  showError("token-error", "");
  const button = el("token-create");
  button.disabled = true;
  try {
    const minted = await postJSON("api/tokens", { label, days: Number(el("token-days").value) });
    showMinted(minted.token);
    el("token-label").value = "";
    await loadTokens();
  } catch (e) {
    showError("token-error", e.message);
  } finally {
    button.disabled = false;
  }
}

function showMinted(value) {
  const url = mcpUrl();
  const name = serverName();
  el("token-value").value = value;
  el("token-cli").textContent =
    `claude mcp add --transport http ${name} ${url} \\\n  --header "Authorization: Bearer ${value}"`;
  el("token-json").textContent = JSON.stringify(
    { mcpServers: { [name]: { url, headers: { Authorization: `Bearer ${value}` } } } }, null, 2);
  el("token-minted").hidden = false;
  el("token-value").select();
}

function forgetMinted() {
  // the value must not linger in the page once the dialog is closed
  el("token-value").value = "";
  for (const id of ["token-cli", "token-json"]) el(id).textContent = "";
  el("token-minted").hidden = true;
}

async function copy(button) {
  const source = el(button.dataset.copy);
  const isInput = source.tagName === "INPUT";
  try {
    await navigator.clipboard.writeText(isInput ? source.value : source.textContent);
    toast("Copied");
  } catch {
    // no clipboard outside a secure context: leave it selected for Ctrl+C
    if (isInput) {
      source.select();
    } else {
      const range = document.createRange();
      range.selectNodeContents(source);
      getSelection().removeAllRanges();
      getSelection().addRange(range);
    }
    toast("Select and copy it by hand", "bad");
  }
}

// ---------- account ----------

function renderAccount(me) {
  const facts = [["Name", me.name], ["Email", me.emails.join(", ") || "—"], ["Role", me.role], ["Since", day(me.created_at)]];
  el("account-facts").replaceChildren(...facts.flatMap(([term, value]) => {
    const dt = document.createElement("dt");
    dt.textContent = term;
    const dd = document.createElement("dd");
    dd.textContent = value;
    return [dt, dd];
  }));
  showError("account-delete-blocked", me.delete_blocked);
  el("account-delete-form").hidden = Boolean(me.delete_blocked);
  el("account-delete-confirm").value = "";
  el("account-delete").disabled = true;
  showError("account-delete-error", "");
}

async function deleteAccount() {
  if (el("account-delete-confirm").value.trim().toLowerCase() !== CONFIRM_WORD) return;
  el("account-delete").disabled = true;
  try {
    await del("api/me");
    location.assign("./");
  } catch (e) {
    showError("account-delete-error", e.message);
    el("account-delete").disabled = false;
  }
}

// ---------- dialog ----------

export async function openSettings() {
  const mcp = store.config.mcp;
  el("set-mcp").hidden = !mcp || !store.session.signed_in;
  forgetMinted();
  showError("token-error", "");
  syncControls();
  el("dlg-settings").showModal();
  const loading = [getJSON("api/me").then(renderAccount).catch((e) => showError("account-delete-error", e.message))];
  if (!el("set-mcp").hidden) loading.push(loadTokens());
  await Promise.all(loading);
}

export function initSettings(s) {
  store = s;
  const mcp = store.config.mcp;
  if (mcp) {
    const select = el("token-days");
    for (const days of mcp.token_days.choices) {
      const option = document.createElement("option");
      option.value = String(days);
      option.textContent = `${days} days`;
      option.selected = days === mcp.token_days.default;
      select.append(option);
    }
  }
  el("token-create").addEventListener("click", createToken);
  el("token-label").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); createToken(); } });
  for (const button of el("dlg-settings").querySelectorAll("button[data-copy]")) {
    button.addEventListener("click", () => copy(button));
  }
  // Enter in a field of a method=dialog form would close the dialog
  el("account-delete-confirm").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); deleteAccount(); } });
  el("account-delete-confirm").addEventListener("input", (e) => {
    el("account-delete").disabled = e.target.value.trim().toLowerCase() !== CONFIRM_WORD;
  });
  el("account-delete").addEventListener("click", deleteAccount);
  el("dlg-settings").addEventListener("close", forgetMinted);
}
