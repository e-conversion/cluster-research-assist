// Admin console. Every call is authorised on the server; this page only
// renders what it is allowed to see.
import { del, getJSON, postJSON } from "./api.js";

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

async function loadUsers() {
  const { users } = await getJSON("api/admin/users");
  const body = $("users");
  body.replaceChildren();
  for (const u of users) {
    const row = el("tr");
    row.append(el("td", null, u.display_name + (u.self ? " (you)" : "")));

    const roleCell = el("td");
    const select = el("select");
    for (const role of ["user", "admin"]) {
      const option = el("option", null, role);
      option.value = role;
      option.selected = u.role === role;
      select.append(option);
    }
    select.disabled = u.self;
    select.addEventListener("change", () =>
      guard(async () => {
        await put(`api/admin/users/${u.id}`, { role: select.value });
        toast(`${u.display_name} is now ${select.value}`);
      }),
    );
    roleCell.append(select);
    if (!u.is_active) roleCell.append(el("span", "src", " disabled"));
    row.append(roleCell);

    row.append(el("td", "desc", u.identities.map((i) => i.organization || i.issuer).join(", ")));
    row.append(el("td", "desc", date(u.last_login_at)));

    const actions = el("td", "actions");
    if (!u.self) {
      actions.append(
        button(u.is_active ? "Disable" : "Enable", "ghost", () =>
          guard(async () => {
            await put(`api/admin/users/${u.id}`, { is_active: !u.is_active });
            await loadUsers();
          }),
        ),
        button("Delete", "ghost danger", () =>
          guard(async () => {
            if (!confirm(`Delete the account of ${u.display_name}? Their history goes with it.`)) return;
            await del(`api/admin/users/${u.id}`);
            await loadUsers();
          }),
        ),
      );
    }
    row.append(actions);
    body.append(row);
  }
}

async function loadEmails() {
  const { emails } = await getJSON("api/admin/emails");
  const body = $("emails");
  body.replaceChildren();
  for (const e of emails) {
    const row = el("tr");
    row.append(el("td", null, e.email));
    row.append(el("td", "desc", e.user_id ? "bound" : "not yet used"));
    row.append(el("td", "desc", e.created_by));
    const actions = el("td", "actions");
    actions.append(
      button("Remove", "ghost danger", () =>
        guard(async () => {
          await del(`api/admin/emails/${encodeURIComponent(e.email)}`);
          await loadEmails();
        }),
      ),
    );
    row.append(actions);
    body.append(row);
  }
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

async function loadLibrary() {
  const info = await getJSON("api/admin/library");
  const counts = Object.entries(info.counts)
    .map(([k, v]) => `${k} ${v}`)
    .join(" · ");
  const built = info.manifest ? info.manifest.built_at : "unknown";
  $("library").replaceChildren(
    el("div", null, counts || "no library loaded"),
    el("div", "desc", `${info.path} · built ${built}`),
  );
}

async function boot() {
  try {
    const session = await getJSON("api/session");
    $("user-label").textContent = session.user || "";
  } catch {
    location.assign("./");
    return;
  }
  await guard(() => Promise.all([loadUsers(), loadEmails(), loadPolicy(), loadLibrary()]));
  $("email-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    guard(async () => {
      await postJSON("api/admin/emails", { email: $("email-input").value.trim() });
      $("email-input").value = "";
      await loadEmails();
    });
  });
}

boot();
