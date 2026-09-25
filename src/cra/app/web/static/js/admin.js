// Admin console. Every call is authorised on the server; this page only
// renders what it is allowed to see.
import { del, getJSON, postJSON } from "./api.js";
import { copyText } from "./clipboard.js";

const $ = (id) => document.getElementById(id);

function toast(message, kind = "") {
  const t = document.createElement("div");
  t.className = "toast " + kind;
  t.textContent = message;
  $("toasts").append(t);
  setTimeout(() => t.remove(), 3200);
}

async function guard(fn) {
  try {
    await fn();
  } catch (e) {
    toast(e.message, "bad");
  }
}

const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};

function button(label, cls, onClick) {
  const b = el("button", `btn ${cls}`, label);
  b.type = "button";
  b.addEventListener("click", onClick);
  return b;
}

const date = (iso) => (iso ? new Date(iso).toLocaleDateString() : "never");

async function put(path, body) {
  const res = await fetch(path, {
    method: "PUT",
    headers: { "content-type": "application/json", accept: "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new Error((data && data.error) || res.statusText);
  return data;
}

function roleSelect(current, { disabled = false, onChange }) {
  const select = el("select");
  for (const role of ["user", "admin"]) {
    const option = el("option", null, role);
    option.value = role;
    option.selected = current === role;
    select.append(option);
  }
  select.disabled = disabled;
  select.addEventListener("change", () => onChange(select.value));
  return select;
}

// ---------- one-time links ----------

function showLink(title, link, hours) {
  $("link-title").textContent = title;
  $("link-value").value = new URL(link, location.origin).href;
  $("link-hint").textContent =
    `Shown once, valid for ${hours} hours and good for one use. Send it to them over a channel you trust.`;
  $("link-box").hidden = false;
  $("link-value").select();
  $("link-box").scrollIntoView({ block: "nearest" });
}

function hideLink() {
  $("link-value").value = "";
  $("link-box").hidden = true;
}

async function copyLink() {
  if (await copyText($("link-value").value)) {
    toast("Copied");
  } else {
    $("link-value").select();
    toast("Select and copy it by hand", "bad");
  }
}

function signInMethods(p) {
  const methods = [];
  if (p.username) methods.push(`password (${p.username})`);
  if (p.sign_in.includes("institution")) methods.push(p.organizations.join(", ") || "institution");
  return methods.join(" · ") || "—";
}

function accountRow(p) {
  const row = el("tr");
  const name = el("td");
  name.append(el("div", null, p.name + (p.self ? " (you)" : "")));
  if (p.email) name.append(el("div", "desc", p.email));
  row.append(name);

  const role = el("td");
  role.append(
    roleSelect(p.role, {
      disabled: p.self,
      onChange: (value) =>
        guard(async () => {
          await put(`api/admin/users/${p.id}`, { role: value });
          toast(`${p.name} is now ${value}`);
          await loadPeople();
        }),
    }),
  );
  if (!p.active) role.append(el("span", "src", " disabled"));
  row.append(role);

  row.append(el("td", "desc", signInMethods(p)));
  row.append(el("td", "desc", date(p.last_login_at)));

  const actions = el("td", "actions");
  if (p.username) {
    actions.append(
      button("Reset password", "ghost", () =>
        guard(async () => {
          if (!confirm(`Reset the password of ${p.name}? Their current password stops working and they are signed out everywhere.`)) return;
          const r = await postJSON(`api/admin/users/${p.id}/password-reset`);
          showLink(`Password reset link for ${p.name}`, r.link, r.expires_in_hours);
        }),
      ),
    );
  }
  if (!p.self) {
    actions.append(
      button(p.active ? "Disable" : "Enable", "ghost", () =>
        guard(async () => {
          await put(`api/admin/users/${p.id}`, { is_active: !p.active });
          await loadPeople();
        }),
      ),
      button("Delete", "ghost danger", () =>
        guard(async () => {
          if (!confirm(`Delete the account of ${p.name}? Their history goes with it.`)) return;
          await del(`api/admin/users/${p.id}`);
          await loadPeople();
        }),
      ),
    );
  }
  row.append(actions);
  return row;
}

function invitationRow(p) {
  const row = el("tr");
  const name = el("td");
  name.append(el("div", null, p.email), el("div", "desc", `invited by ${p.invited_by}`));
  row.append(name);

  const role = el("td");
  role.append(
    roleSelect(p.role, {
      onChange: (value) =>
        guard(async () => {
          await put(`api/admin/emails/${encodeURIComponent(p.email)}`, { role: value });
          await loadPeople();
        }),
    }),
  );
  row.append(role);
  row.append(el("td", "desc", "invited, not yet signed in"));
  row.append(el("td", "desc", date(p.invited_at)));

  const actions = el("td", "actions");
  actions.append(
    button("Revoke", "ghost danger", () =>
      guard(async () => {
        await del(`api/admin/emails/${encodeURIComponent(p.email)}`);
        await loadPeople();
      }),
    ),
  );
  row.append(actions);
  return row;
}

function configuredRow(p) {
  const row = el("tr");
  row.append(el("td", null, p.email));
  row.append(el("td", "desc", `${p.role} by configuration`));
  row.append(el("td", "desc", "CRA_AUTH_ADMINS"));
  row.append(el("td", "desc", "never"));
  row.append(el("td", "actions"));
  return row;
}

const ROWS = { account: accountRow, invitation: invitationRow, configured: configuredRow };

async function loadPeople() {
  const { people } = await getJSON("api/admin/people");
  const body = $("people");
  body.replaceChildren();
  for (const person of people) body.append(ROWS[person.kind](person));
}

// ---------- access requests ----------

function requestRow(r) {
  const row = el("tr");
  const person = el("td");
  person.append(el("div", null, r.name), el("div", "desc", r.email || "no email released"), el("div", "desc", r.institution));
  row.append(person);

  const group = el("td");
  group.append(el("div", null, r.group));
  if (!r.group_in_library) group.append(el("div", "desc", "not in the library"));
  if (r.message) group.append(el("div", "desc", `“${r.message}”`));
  row.append(group);

  const page = el("td", "desc");
  let host;
  try { host = new URL(r.profile_url).host; } catch { host = null; }
  // one link the browser cannot parse must not take the whole list with it
  if (host === null) page.append(el("span", null, r.profile_url));
  const link = el("a", null, host);
  // the link is the requester's text: opened only by the admin's own click,
  // never fetched, and without telling that page where the click came from
  link.href = r.profile_url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.title = r.profile_url;
  if (host !== null) page.append(link);
  row.append(page);

  row.append(el("td", "desc", date(r.created_at)));

  const actions = el("td", "actions");
  if (r.status === "pending") {
    let role = "user";
    actions.append(
      roleSelect(role, { onChange: (value) => { role = value; } }),
      button("Approve", "primary", () =>
        guard(async () => {
          await postJSON(`api/admin/access-requests/${r.id}/approve`, { role });
          toast(`${r.name} can sign in now`);
          await Promise.all([loadRequests(), loadPeople()]);
        }),
      ),
      button("Decline", "ghost danger", () =>
        guard(async () => {
          const note = prompt(`Decline ${r.name}'s request. A note they will see (optional):`, "");
          if (note === null) return;
          await postJSON(`api/admin/access-requests/${r.id}/reject`, { note });
          await loadRequests();
        }),
      ),
    );
  } else {
    actions.append(
      el("span", "desc", `${r.status} by ${r.decided_by} ${date(r.decided_at)} `),
      button("Delete", "ghost", () =>
        guard(async () => {
          if (!confirm(`Delete ${r.name}'s request? They could then ask again.`)) return;
          await del(`api/admin/access-requests/${r.id}`);
          await loadRequests();
        }),
      ),
    );
  }
  row.append(actions);
  return row;
}

async function loadRequests() {
  const { requests, pending } = await getJSON("api/admin/access-requests");
  const count = $("requests-count");
  count.textContent = String(pending);
  count.hidden = !pending;
  const body = $("requests");
  if (!requests.length) {
    const cell = el("td", "desc", "Nobody has asked.");
    cell.colSpan = 5;
    const empty = el("tr");
    empty.append(cell);
    body.replaceChildren(empty);
    return;
  }
  body.replaceChildren(...requests.map(requestRow));
}

async function loadPolicy() {
  const { settings } = await getJSON("api/admin/policy");
  const body = $("policy");
  body.replaceChildren();
  for (const s of settings) {
    const row = el("tr");
    const name = el("td");
    name.append(el("div", null, s.key.replace(/_/g, " ")), el("div", "desc", s.description));
    row.append(name);

    const valueCell = el("td");
    const input = el("input", "val");
    input.value = Array.isArray(s.value) ? s.value.join(", ") : String(s.value);
    if (typeof s.value === "boolean") input.setAttribute("list", "booleans");
    input.addEventListener("change", () =>
      guard(async () => {
        await put(`api/admin/policy/${s.key}`, { value: input.value });
        toast(`${s.key} saved`);
        await loadPolicy();
      }),
    );
    valueCell.append(input);
    row.append(valueCell);

    row.append(el("td", `src ${s.source === "database" ? "db" : ""}`, s.source));

    const actions = el("td", "actions");
    if (s.source === "database") {
      actions.append(
        button("Reset", "ghost", () =>
          guard(async () => {
            await put(`api/admin/policy/${s.key}`, { reset: true });
            await loadPolicy();
          }),
        ),
      );
    }
    row.append(actions);
    body.append(row);
  }
}

async function loadFeedback() {
  const { feedback } = await getJSON("api/admin/feedback");
  const body = $("feedback");
  body.replaceChildren();
  if (!feedback.length) {
    const empty = el("tr");
    const cell = el("td", "desc", "Nothing yet.");
    cell.colSpan = 5;
    empty.append(cell);
    body.append(empty);
    return;
  }
  for (const f of feedback) {
    const row = el("tr");
    row.append(el("td", null, f.from));
    row.append(el("td", "desc", f.category));

    const note = el("td");
    note.append(el("div", null, f.text));
    if (f.messages.length) {
      const details = el("details");
      details.append(el("summary", "desc", `${f.messages.length} messages`));
      for (const m of f.messages) {
        const line = el("div", "desc");
        line.append(el("b", null, `${m.role}: `), document.createTextNode(m.content));
        details.append(line);
      }
      note.append(details);
    }
    if (f.model) note.append(el("div", "src", f.model));
    row.append(note);

    row.append(el("td", "desc", date(f.created_at)));
    const actions = el("td", "actions");
    actions.append(
      button("Delete", "ghost danger", () =>
        guard(async () => {
          await del(`api/admin/feedback/${f.id}`);
          await loadFeedback();
        }),
      ),
    );
    row.append(actions);
    body.append(row);
  }
}

async function loadTokens() {
  const { tokens } = await getJSON("api/admin/tokens");
  const body = $("tokens");
  body.replaceChildren();
  if (!tokens.length) {
    const empty = el("tr");
    const cell = el("td", "desc", "Nobody holds a token.");
    cell.colSpan = 6;
    empty.append(cell);
    body.append(empty);
    return;
  }
  for (const t of tokens) {
    const row = el("tr");
    row.append(el("td", null, t.owner));
    row.append(el("td", null, t.label));
    row.append(el("td", "desc", date(t.created_at)));
    row.append(el("td", "desc", date(t.expires_at)));
    row.append(el("td", "desc", date(t.last_used_at)));
    const actions = el("td", "actions");
    if (t.state === "active") {
      actions.append(
        button("Revoke", "ghost danger", () =>
          guard(async () => {
            await del(`api/admin/tokens/${encodeURIComponent(t.id)}`);
            toast(`revoked ${t.owner}'s “${t.label}”`);
            await loadTokens();
          }),
        ),
      );
    } else {
      actions.append(el("span", "desc", t.state));
    }
    row.append(actions);
    body.append(row);
  }
}

const megabytes = (n) => `${(n / 1e6).toFixed(1)} MB`;

async function loadLibrary() {
  const info = await getJSON("api/admin/library");
  const counts = Object.entries(info.counts)
    .map(([k, v]) => `${k} ${v}`)
    .join(" · ");
  const built = info.manifest ? info.manifest.built_at : "unknown";
  $("library").replaceChildren(
    el("div", null, counts || "no library loaded"),
    el("div", "desc", `${info.active || info.path} · built ${built}`),
  );

  $("versions-table").hidden = !info.updatable;
  $("upload-form").hidden = !info.updatable;
  const hint = $("upload-hint");
  hint.hidden = false;
  hint.textContent = info.updatable
    ? `A bundle is verified in full before it replaces the live one, and the previous versions stay so you can switch back. Up to ${info.max_upload_mb} MB.`
    : "This deployment points at a fixed library directory. Run `cra library init-root` to make it updatable.";

  const body = $("versions");
  body.replaceChildren();
  for (const v of info.versions) {
    const row = el("tr");
    row.append(el("td", null, v.name));
    row.append(el("td", "desc", megabytes(v.bytes)));
    row.append(el("td", "desc", v.active ? "live" : ""));
    const actions = el("td", "actions");
    if (!v.active) {
      actions.append(
        button("Switch to this", "ghost", () =>
          guard(async () => {
            await postJSON(`api/admin/library/${encodeURIComponent(v.name)}/activate`);
            toast(`now serving ${v.name}`);
            await loadLibrary();
          }),
        ),
      );
    }
    row.append(actions);
    body.append(row);
  }
}

async function uploadLibrary(file) {
  const form = new FormData();
  form.append("bundle", file);
  // the one multipart request; the header marks it as ours, not a form's
  const res = await fetch("api/admin/library", {
    method: "POST", body: form, headers: { "x-requested-with": "cra" },
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new Error((data && data.error) || res.statusText);
  return data;
}

const TABS = ["accounts", "requests", "settings", "feedback", "tokens", "library"];

/** The open tab lives in the URL fragment, so a reload keeps it. */
function showTab(name) {
  const open = TABS.includes(name) ? name : TABS[0];
  if (open !== "accounts") hideLink();
  // requests arrive while the console is open; show the current ones
  if (open === "requests" && !$("tabs").querySelector('[data-tab="requests"]').hidden) guard(loadRequests);
  for (const tab of TABS) $(`tab-${tab}`).hidden = tab !== open;
  for (const b of $("tabs").querySelectorAll("button")) {
    b.classList.toggle("on", b.dataset.tab === open);
    b.setAttribute("aria-selected", String(b.dataset.tab === open));
  }
}

function initTabs() {
  $("tabs").addEventListener("click", (ev) => {
    const b = ev.target.closest("button[data-tab]");
    if (b) location.hash = `#${b.dataset.tab}`;
  });
  addEventListener("hashchange", () => showTab(location.hash.slice(1)));
  showTab(location.hash.slice(1));
}

async function boot() {
  initTabs();
  try {
    const session = await getJSON("api/session");
    $("user-label").textContent = session.user || "";
  } catch {
    location.assign("./");
    return;
  }
  // tokens exist only where the deployment serves the MCP endpoint
  const config = await getJSON("api/config").catch(() => ({}));
  const tokensTab = $("tabs").querySelector('[data-tab="tokens"]');
  tokensTab.hidden = !config.mcp;
  // without institutional sign-in there is nobody to invite or to ask
  const institution = Boolean(config.auth && config.auth.institution);
  $("tabs").querySelector('[data-tab="requests"]').hidden = !institution;
  for (const id of ["invite-heading", "invite-desc", "invite-form"]) $(id).hidden = !institution;
  await guard(() =>
    Promise.all([
      loadPeople(),
      institution ? loadRequests() : null,
      loadPolicy(),
      loadFeedback(),
      config.mcp ? loadTokens() : null,
      loadLibrary(),
    ]),
  );
  $("upload-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const file = $("bundle-input").files[0];
    if (!file) return;
    const submit = ev.target.querySelector("button");
    submit.disabled = true;
    submit.textContent = "Uploading…";
    guard(async () => {
      const r = await uploadLibrary(file);
      toast(`now serving ${r.version}: ${r.counts.papers} papers`);
      $("bundle-input").value = "";
      await loadLibrary();
    }).finally(() => {
      submit.disabled = false;
      submit.textContent = "Upload and activate";
    });
  });
  $("link-copy").addEventListener("click", copyLink);
  $("local-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    guard(async () => {
      const name = $("local-name").value.trim();
      const r = await postJSON("api/admin/local-users", {
        name,
        username: $("local-username").value.trim(),
        email: $("local-email").value.trim(),
        role: $("local-role").value,
      });
      ev.target.reset();
      await loadPeople();
      showLink(`Set-password link for ${name}`, r.link, r.expires_in_hours);
    });
  });
  $("invite-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    guard(async () => {
      await postJSON("api/admin/emails", {
        email: $("invite-email").value.trim(),
        role: $("invite-role").value,
      });
      $("invite-email").value = "";
      await loadPeople();
    });
  });
}

boot();
