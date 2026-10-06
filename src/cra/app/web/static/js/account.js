// The Settings dialog: appearance, connecting apps (mcp.js) and the account
// itself.
import { getJSON, patchJSON, putJSON, del } from "./api.js";
import { syncControls } from "./theme.js";
import { toast } from "./toast.js";
import { forgetMinted, initMcp, loadTokens, showMcp } from "./mcp.js";

const CONFIRM_WORD = "delete";
const el = (id) => document.getElementById(id);
let store = null;

// the server stores naive UTC timestamps
const parseUtc = (iso) => new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : iso + "Z");
const day = (iso) =>
  iso ? parseUtc(iso).toLocaleDateString(undefined, { dateStyle: "medium" }) : "—";

function showError(id, message) {
  const p = el(id);
  p.textContent = message || "";
  p.hidden = !message;
}

// Settings has no single apply: Enter in a field runs its own section's action
function onEnter(ids, action) {
  for (const id of ids) {
    el(id).addEventListener("keydown", (e) => {
      if (e.key !== "Enter") return;
      e.preventDefault();
      action();
    });
  }
}

// ---------- profile ----------

const PROFILE_FIELDS = {
  name: "profile-name",
  username: "profile-username",
  email: "profile-email",
};
// what the server holds; an institutional account has no username
let savedProfile = {};

function profileChanges() {
  const changes = {};
  for (const [key, value] of Object.entries(savedProfile)) {
    const typed = el(PROFILE_FIELDS[key]).value.trim();
    if (typed !== value) changes[key] = typed;
  }
  return changes;
}

function syncProfileActions() {
  const dirty = Object.keys(profileChanges()).length > 0;
  el("profile-save").disabled = !dirty;
  el("profile-reset").hidden = !dirty;
}

function resetProfile() {
  for (const [key, value] of Object.entries(savedProfile)) el(PROFILE_FIELDS[key]).value = value;
  showError("profile-error", "");
  syncProfileActions();
}

function renderProfile(me) {
  savedProfile = { name: me.name, email: me.email };
  if (me.username) savedProfile.username = me.username;
  el("profile-username-field").hidden = !me.username;
  el("profile-hint").textContent = me.username
    ? "You sign in with your username or your email address."
    : "You sign in at your institution; the address here is only for reaching you.";
  resetProfile();
}

async function saveProfile() {
  const changes = profileChanges();
  if (!Object.keys(changes).length) return;
  el("profile-save").disabled = true;
  try {
    renderProfile(await patchJSON("api/me", changes));
    toast("Profile saved");
  } catch (e) {
    showError("profile-error", e.message);
    syncProfileActions();
  }
}

// ---------- account ----------

function renderAccount(me) {
  renderProfile(me);
  const facts = [
    ["Role", me.role],
    ["Member since", day(me.created_at)],
  ];
  if (me.sign_in_emails.length) facts.unshift(["Invited as", me.sign_in_emails.join(", ")]);
  el("account-facts").replaceChildren(
    ...facts.flatMap(([term, value]) => {
      const dt = document.createElement("dt");
      dt.textContent = term;
      const dd = document.createElement("dd");
      dd.textContent = value;
      return [dt, dd];
    }),
  );
  // only a password account has a password to change
  el("account-password").hidden = !me.username;
  forgetPasswords();
  showError("account-delete-blocked", me.delete_blocked);
  el("account-delete-form").hidden = Boolean(me.delete_blocked);
  el("account-delete-confirm").value = "";
  el("account-delete").disabled = true;
  showError("account-delete-error", "");
}

const PASSWORD_FIELDS = ["password-current", "password-new", "password-again"];

function forgetPasswords() {
  for (const id of PASSWORD_FIELDS) el(id).value = "";
  showError("password-error", "");
}

async function changePassword() {
  const current = el("password-current").value;
  const next = el("password-new").value;
  if (next !== el("password-again").value) {
    showError("password-error", "The two new passwords differ.");
    return;
  }
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
  if (mcpOffered()) {
    forgetMinted();
    showMcp();
  }
  syncControls();
  showTab(openTab);
  el("dlg-settings").showModal();
  const loading = [
    getJSON("api/me")
      .then(renderAccount)
      .catch((e) => showError("account-delete-error", e.message)),
  ];
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
  for (const id of Object.values(PROFILE_FIELDS)) {
    el(id).addEventListener("input", syncProfileActions);
  }
  onEnter(Object.values(PROFILE_FIELDS), saveProfile);
  el("profile-save").addEventListener("click", saveProfile);
  el("profile-reset").addEventListener("click", resetProfile);
  onEnter(["account-delete-confirm"], deleteAccount);
  el("account-delete-confirm").addEventListener("input", (e) => {
    el("account-delete").disabled = e.target.value.trim().toLowerCase() !== CONFIRM_WORD;
  });
  el("account-delete").addEventListener("click", deleteAccount);
  el("password-change").addEventListener("click", changePassword);
  onEnter(PASSWORD_FIELDS, changePassword);
  el("dlg-settings").addEventListener("close", () => {
    if (mcpOffered()) forgetMinted();
    forgetPasswords();
    resetProfile();
  });
}
