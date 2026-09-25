// Bootstrap: load config + session, then start the router.
import { ApiError, getJSON, postJSON } from "./api.js";
import { createRouter } from "./router.js";
import { chatView } from "./chat.js";
import { initDialogs, toast } from "./settings.js";
import { initHistory } from "./history.js";
import { libraryMapView } from "./views/publication-map.js";
import { collaborationView } from "./views/collaboration-graph.js";
import { isAccessRequest, isPasswordLink, renderAccessRequest, renderSetPassword, renderSignIn } from "./signin.js";

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
  document.getElementById("sign-out").hidden = !s.signed_in;
  document.getElementById("admin-link").hidden = !s.is_admin;
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
  if (config.notice) {
    const bar = document.getElementById("notice");
    bar.textContent = config.notice;
    bar.hidden = false;
  }
  // a set-password link works whoever is signed in in this browser
  if (isPasswordLink()) { await renderSetPassword(view, config); return; }
  let session;
  try {
    session = await getJSON("api/session");
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) {
      if (isAccessRequest()) await renderAccessRequest(view, config);
      else renderSignIn(view, config);
      return;
    }
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
