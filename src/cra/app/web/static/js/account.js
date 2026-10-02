// The Settings dialog: appearance, connecting apps (mcp.js) and the account
// itself.
import { getJSON, putJSON, del } from "./api.js";
import { syncControls } from "./theme.js";
import { toast } from "./toast.js";
import { forgetMinted, initMcp, loadTokens, showMcp } from "./mcp.js";

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

// ---------- account ----------

function renderAccount(me) {
  const facts = [["Name", me.name], ["Email", me.emails.join(", ") || "—"], ["Role", me.role], ["Since", day(me.created_at)]];
  if (me.username) facts.splice(1, 0, ["Username", me.username]);
  el("account-facts").replaceChildren(...facts.flatMap(([term, value]) => {
    const dt = document.createElement("dt");
    dt.textContent = term;
    const dd = document.createElement("dd");
    dd.textContent = value;
    return [dt, dd];
  }));
  // only a password account has a password to change
  el("account-password").hidden = !me.username;
  el("account-username").value = me.username || "";
  forgetPasswords();
  showError("account-delete-blocked", me.delete_blocked);
  el("account-delete-form").hidden = Boolean(me.delete_blocked);
  el("account-delete-confirm").value = "";
  el("account-delete").disabled = true;
  showError("account-delete-error", "");
}

function forgetPasswords() {
  for (const id of ["password-current", "password-new", "password-again"]) el(id).value = "";
  showError("password-error", "");
}

async function changePassword() {
  const current = el("password-current").value;
  const next = el("password-new").value;
  if (next !== el("password-again").value) { showError("password-error", "The two new passwords differ."); return; }
  const button = el("password-change");
  button.disabled = true;
  try {
    await putJSON("api/me/password", { current, new: next });
    forgetPasswords();
    toast("Password changed");
  } catch (e) {
    showError("password-error", e.message);
  } finally {
    button.disabled = false;
  }
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

const TABS = ["appearance", "mcp", "account"];
let openTab = TABS[0];

function mcpOffered() {
  return Boolean(store.config.mcp) && store.session.signed_in;
}

function showTab(name) {
  openTab = TABS.includes(name) && (name !== "mcp" || mcpOffered()) ? name : TABS[0];
  for (const tab of TABS) el(`set-${tab}`).hidden = tab !== openTab;
  for (const b of el("settings-tabs").querySelectorAll("button[data-tab]")) {
    b.classList.toggle("on", b.dataset.tab === openTab);
    b.setAttribute("aria-selected", String(b.dataset.tab === openTab));
  }
}

/** Opens on the tab it was last left on; each tab loads what it shows. */
export async function openSettings() {
  el("settings-tab-mcp").hidden = !mcpOffered();
  if (mcpOffered()) { forgetMinted(); showMcp(); }
  syncControls();
  showTab(openTab);
  el("dlg-settings").showModal();
  const loading = [getJSON("api/me").then(renderAccount).catch((e) => showError("account-delete-error", e.message))];
  if (mcpOffered()) loading.push(loadTokens());
  await Promise.all(loading);
}

export function initSettings(s) {
  store = s;
  initMcp(s);
  el("settings-tabs").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-tab]");
    if (b) showTab(b.dataset.tab);
  });
  // Enter in a field of a method=dialog form would close the dialog
  el("account-delete-confirm").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); deleteAccount(); } });
  el("account-delete-confirm").addEventListener("input", (e) => {
    el("account-delete").disabled = e.target.value.trim().toLowerCase() !== CONFIRM_WORD;
  });
  el("account-delete").addEventListener("click", deleteAccount);
  el("password-change").addEventListener("click", changePassword);
  for (const id of ["password-current", "password-new", "password-again"]) {
    el(id).addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); changePassword(); } });
  }
  el("dlg-settings").addEventListener("close", () => {
    if (mcpOffered()) forgetMinted();
    forgetPasswords();
  });
}
