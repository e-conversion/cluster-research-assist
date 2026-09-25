// The pages someone sees before they are signed in: the sign-in card, the
// access request that follows an institutional sign-in without an account,
// and the page a one-time set-password link opens. Everything the server or
// the person sent is set as text, never as markup.
import { ApiError, getJSON, postJSON } from "./api.js";
import { logo } from "./brand.js";

const SET_PASSWORD = "#/set-password/";
const REQUEST_ACCESS = "#/request-access";

function make(tag, props = {}, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
}

function field(label, input) {
  return make("label", { className: "field" }, make("span", { textContent: label }), input);
}

function errorLine() {
  return make("p", { className: "error", hidden: true, role: "alert" });
}

function showError(line, message) {
  line.textContent = message || "";
  line.hidden = !message;
}

function card(config, ...children) {
  return make("div", { className: "signin" }, logo(), make("h1", { textContent: config.title }), ...children);
}

function contactLine(config, prefix) {
  const who = config.auth.contact || "the administrators";
  return make("p", { className: "muted small", textContent: `${prefix} ${who}.` });
}

function hideChrome() {
  for (const id of ["nav", "new-chat", "menu"]) document.getElementById(id).hidden = true;
}

/** Reloads onto the app itself, without whatever the address still carries. */
function enter() {
  history.replaceState(null, "", location.pathname + location.search);
  location.reload();
}

// ---------- sign in ----------

function passwordForm() {
  const username = make("input", { type: "text", name: "username", autocomplete: "username", required: true, autocapitalize: "none", spellcheck: false });
  const password = make("input", { type: "password", name: "password", autocomplete: "current-password", required: true });
  const submit = make("button", { type: "submit", className: "btn primary block", textContent: "Sign in" });
  const error = errorLine();
  const form = make("form", { className: "signin-form" }, field("Username", username), field("Password", password), submit, error);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    submit.disabled = true;
    showError(error, "");
    try {
      await postJSON("auth/password", { username: username.value, password: password.value });
      enter();
    } catch (err) {
      password.value = "";
      showError(error, err.message);
      submit.disabled = false;
      password.focus();
    }
  });
  return form;
}

function institutionButton(config, text) {
  return make("a", { className: "btn block", href: config.auth.login_url, textContent: text });
}

function tabbed(panels) {
  const bar = make("div", { className: "tabs", role: "tablist" });
  const buttons = panels.map(([label, panel], i) => {
    const button = make("button", { type: "button", role: "tab", textContent: label });
    button.addEventListener("click", () => select(i));
    return button;
  });
  function select(index) {
    panels.forEach(([, panel], i) => { panel.hidden = i !== index; });
    buttons.forEach((b, i) => { b.classList.toggle("on", i === index); b.setAttribute("aria-selected", String(i === index)); });
  }
  bar.append(...buttons);
  select(0);
  return [bar, ...panels.map(([, panel]) => panel)];
}

// The sign-in page follows the system theme; the choice between light and
// dark is offered in Settings, once there is someone to remember it for.
export function renderSignIn(view, config) {
  const lede = make("p", {
    className: "lede",
    textContent: `Ask about the research in ${config.cluster.name}: its papers, its groups and who works with whom.`,
  });
  let body;
  if (config.auth.institution) {
    const signIn = make("div", { className: "signin-panel" },
      passwordForm(),
      make("div", { className: "or", textContent: "or" }),
      institutionButton(config, "Sign in with your institution"),
      make("p", { className: "muted small", textContent: "Through DFN-AAI, with your university account." }));
    const request = make("div", { className: "signin-panel" },
      make("p", { className: "explain", textContent:
        "Members of the cluster can ask for an account. Sign in at your university first; " +
        "you then tell us your group and a page at your institution that shows it, " +
        "and an admin reviews the request." }),
      institutionButton(config, "Continue with your institution"),
      contactLine(config, "Questions:"));
    body = tabbed([["Sign in", signIn], ["Request access", request]]);
  } else {
    body = [passwordForm()];
  }
  view.replaceChildren(card(config, lede, ...body));
  hideChrome();
  view.querySelector("input[name=username]").focus();
}

// ---------- set a password from a one-time link ----------

export function isPasswordLink() {
  return location.hash.startsWith(SET_PASSWORD);
}

export async function renderSetPassword(view, config) {
  let token;
  try { token = decodeURIComponent(location.hash.slice(SET_PASSWORD.length)); } catch { token = ""; }
  // out of the address bar and the history entry before anything else happens
  history.replaceState(null, "", location.pathname + location.search);
  hideChrome();
  let username;
  try {
    ({ username } = await postJSON("auth/password-link", { token }));
  } catch (e) {
    view.replaceChildren(card(config, make("p", { className: "note", textContent: e.message })));
    return;
  }
  const password = make("input", { type: "password", autocomplete: "new-password", required: true, minLength: 12 });
  const again = make("input", { type: "password", autocomplete: "new-password", required: true, minLength: 12 });
  const submit = make("button", { type: "submit", className: "btn primary block", textContent: "Set password and sign in" });
  const error = errorLine();
  const form = make("form", { className: "signin-form" },
    field("New password (at least 12 characters)", password), field("Again", again), submit, error);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (password.value !== again.value) { showError(error, "The two passwords differ."); return; }
    submit.disabled = true;
    showError(error, "");
    try {
      await postJSON("auth/set-password", { token, password: password.value });
      enter();
    } catch (err) {
      showError(error, err.message);
      submit.disabled = false;
    }
  });
  // say whose account this is, so nobody sets a password on one sent to them
  const lede = make("p", { className: "lede" }, "Choose a password for the account ",
    make("b", { textContent: username }), ". The link works once.");
  view.replaceChildren(card(config, lede, form));
  password.focus();
}

// ---------- asking for an account ----------

export function isAccessRequest() {
  return location.hash === REQUEST_ACCESS;
}

const parseUtc = (iso) => new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : iso + "Z");
const day = (iso) => parseUtc(iso).toLocaleDateString(undefined, { dateStyle: "medium" });

function requestStatus(config, request) {
  if (request.status === "rejected") {
    const parts = [make("p", { className: "lede", textContent: `Your request of ${day(request.created_at)} was declined.` })];
    if (request.note) parts.push(make("p", { className: "note info", textContent: request.note }));
    parts.push(contactLine(config, "If you think this is a mistake, contact"));
    return parts;
  }
  if (request.status === "approved") {
    return [make("p", { className: "lede", textContent: "Your request was approved." }),
      institutionButton(config, "Sign in with your institution")];
  }
  return [make("p", { className: "lede", textContent:
    `Your request of ${day(request.created_at)} is waiting for an admin. Sign in again once it is approved.` }),
  contactLine(config, "Questions:")];
}

function identityFacts(identity) {
  const rows = [["Name", identity.name], ["Email", identity.email || "not released by your university"], ["Institution", identity.institution || "—"]];
  return make("dl", { className: "account-facts" }, ...rows.flatMap(([term, value]) =>
    [make("dt", { textContent: term }), make("dd", { textContent: value })]));
}

function requestForm(view, config, body) {
  const OTHER = "__other__";
  const group = make("select", { required: true },
    make("option", { value: "", textContent: "Choose your group…" }),
    ...body.groups.map((g) => make("option", { value: g.smid, textContent: g.label })),
    make("option", { value: OTHER, textContent: "Other (not listed)…" }));
  const other = make("input", { type: "text", maxLength: 300, placeholder: "Group or PI" });
  const otherField = field("Your group", other);
  otherField.hidden = true;
  group.addEventListener("change", () => { otherField.hidden = group.value !== OTHER; });
  const profile = make("input", { type: "url", required: true, maxLength: 500, placeholder: "https://…" });
  const message = make("textarea", { rows: 3, maxLength: 1000 });
  const submit = make("button", { type: "submit", className: "btn primary block", textContent: "Send request" });
  const error = errorLine();
  const form = make("form", { className: "signin-form" },
    field("Group", group), otherField,
    field("Your page at your institution (shows that you belong to the group)", profile),
    field("Anything else we should know (optional)", message),
    submit, error);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    submit.disabled = true;
    showError(error, "");
    const picked = group.value === OTHER ? "" : group.value;
    try {
      const sent = await postJSON("api/access-requests", {
        group: picked,
        group_other: picked ? "" : other.value,
        profile_url: profile.value,
        message: message.value,
      });
      view.replaceChildren(card(config, ...requestStatus(config, sent.request)));
    } catch (err) {
      showError(error, err.message);
      submit.disabled = false;
    }
  });
  return form;
}

export async function renderAccessRequest(view, config) {
  hideChrome();
  let body;
  try {
    body = await getJSON("api/access-requests/me");
  } catch (e) {
    if (e instanceof ApiError && e.status === 403 && config.auth.institution) {
      view.replaceChildren(card(config,
        make("p", { className: "lede", textContent: "To ask for an account, sign in with your institution first." }),
        institutionButton(config, "Continue with your institution")));
      return;
    }
    view.replaceChildren(card(config, make("p", { className: "note", textContent: e.message })));
    return;
  }
  if (body.request) {
    view.replaceChildren(card(config, ...requestStatus(config, body.request)));
    return;
  }
  view.replaceChildren(card(config,
    make("p", { className: "lede", textContent: "You signed in at your institution, but have no account here yet. Ask for one:" }),
    identityFacts(body.identity),
    requestForm(view, config, body)));
  view.querySelector(".signin").classList.add("wide");
}
