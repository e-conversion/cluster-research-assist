// Bootstrap: load config + session, then start the router.
import { ApiError, getJSON, postJSON } from "./api.js";
import { createRouter } from "./router.js";
import { chatView } from "./chat.js";
import { initDialogs, toast } from "./settings.js";
import { initHistory } from "./history.js";
import { themeControl } from "./theme.js";
import { libraryMapView } from "./views/publication-map.js";
import { collaborationView } from "./views/collaboration-graph.js";

export const store = {
  config: null,
  session: null,
  streaming: false,     // a chat turn is in flight in this tab
  stop: null,           // function that aborts it
  listeners: new Set(),
  update(patch) { Object.assign(this, patch); for (const fn of this.listeners) fn(this, patch); },
  subscribe(fn) { this.listeners.add(fn); return () => this.listeners.delete(fn); },
};

export async function refreshSession() {
  const session = await getJSON("api/session");
  store.update({ session });
  renderIdentity();
  return session;
}

/** Header state follows the session: anonymous visitors see "Sign in". */
function renderIdentity() {
  const s = store.session || {};
  const label = document.getElementById("user-label");
  label.textContent = s.user || "";
  label.hidden = !s.user;
  document.getElementById("sign-out").hidden = !s.signed_in;
  document.getElementById("admin-link").hidden = !s.is_admin;
}

function renderSignIn(view, config) {
  const page = document.createElement("div");
  page.className = "landing signin";
  page.innerHTML = `
    <div class="landing-head">
      <svg class="logo" aria-hidden="true"><use href="#logo-mark"/></svg>
      <h1>Sign in to ${config.title}</h1>
    </div>
    <p class="lede">The assistant answers questions about the research in ${config.cluster.name}.
    Sign in with your institutional account to start.</p>
    <p><a class="btn primary" href="${config.auth.login_url}">Sign in</a></p>`;
  const appearance = document.createElement("p");
  appearance.className = "signin-theme";
  appearance.append(themeControl());
  page.append(appearance);
  view.replaceChildren(page);
  for (const id of ["nav", "new-chat", "menu"]) document.getElementById(id).hidden = true;
}

// the message may echo whatever the server (or a proxy) sent: text, never markup
function renderUnreachable(view, error) {
  const page = document.createElement("div");
  page.className = "page";
  const note = document.createElement("p");
  note.className = "note";
  note.textContent = `Could not reach the server: ${error.message}`;
  page.append(note);
  view.replaceChildren(page);
}

async function boot() {
  const view = document.getElementById("view");
  let config;
  try {
    config = await getJSON("api/config");
  } catch (e) {
    renderUnreachable(view, e);
    return;
  }
  document.title = config.title;
  document.querySelector(".brand-name").textContent = config.title;
  if (config.notice) {
    const bar = document.getElementById("notice");
    bar.textContent = config.notice;
    bar.hidden = false;
  }
  let session;
  try {
    session = await getJSON("api/session");
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) { renderSignIn(view, config); return; }
    renderUnreachable(view, e);
    return;
  }
  store.update({ config, session });
  renderIdentity();
  document.getElementById("sign-out").addEventListener("click", async () => {
    try {
      const r = await postJSON(store.config.auth.logout_url);
      location.assign(r.redirect);
    } catch (e) { toast(e.message, "bad"); }
  });

  initDialogs(store);
  // reopening a conversation replaces what the chat view is rendering
  initHistory(store, {
    toast,
    onOpen: async () => {
      await refreshSession();
      location.hash = "#/chat";
      store.update({ resetTick: (store.resetTick || 0) + 1 });
    },
  });
  document.getElementById("new-chat").addEventListener("click", async () => {
    if (store.streaming && store.stop) { store.stop(); await new Promise((r) => setTimeout(r, 150)); }
    try {
      await postJSON("api/chat/reset");
      await refreshSession();
      location.hash = "#/chat";
      store.update({ resetTick: (store.resetTick || 0) + 1 });
    } catch (e) { toast(e.message, "bad"); }
  });

  const router = createRouter({
    "chat": chatView(store),
    "publication-map": libraryMapView(store),
    "collaboration-graph": collaborationView(store),
  }, { container: view, nav: document.getElementById("nav") });
  router.start();
}

boot();
