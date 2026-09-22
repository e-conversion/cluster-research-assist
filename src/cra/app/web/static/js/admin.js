// Admin console. Every call is authorised on the server; this page only
// renders what it is allowed to see.
import { del, getJSON, postJSON } from "./api.js";
import { themeControl } from "./theme.js";

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

  row.append(el("td", "desc", p.organizations.join(", ")));
  row.append(el("td", "desc", date(p.last_login_at)));

  const actions = el("td", "actions");
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
  const res = await fetch("api/admin/library", { method: "POST", body: form });
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new Error((data && data.error) || res.statusText);
  return data;
}

const TABS = ["accounts", "settings", "library"];

/** The open tab lives in the URL fragment, so a reload keeps it. */
function showTab(name) {
  const open = TABS.includes(name) ? name : TABS[0];
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
  $("theme-slot").append(themeControl());
  initTabs();
  try {
    const session = await getJSON("api/session");
    $("user-label").textContent = session.user || "";
  } catch {
    location.assign("./");
    return;
  }
  await guard(() => Promise.all([loadPeople(), loadPolicy(), loadLibrary()]));
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
