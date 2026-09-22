// Bootstrap: load config + session, then start the router.
import { ApiError, getJSON, postJSON } from "./api.js";
import { createRouter } from "./router.js";
import { chatView } from "./chat.js";
import { initDialogs, toast } from "./settings.js";
import { libraryMapView } from "./views/publication-map.js";
import { collaborationView } from "./views/collaboration.js";

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
  document.getElementById("sign-in").hidden = !!s.signed_in;
  document.getElementById("sign-out").hidden = !s.signed_in;
  document.getElementById("admin-link").hidden = !s.is_admin;
}

async function boot() {
  const view = document.getElementById("view");
  let config, session;
  try {
    [config, session] = await Promise.all([getJSON("api/config"), getJSON("api/session")]);
  } catch (e) {
    view.innerHTML = `<div class="page"><p class="note">Could not reach the server: ${e.message}</p></div>`;
    return;
  }
  store.update({ config, session });
  document.title = config.title;
  document.querySelector(".brand-name").textContent = config.title;
  if (config.notice) {
    const bar = document.getElementById("notice");
    bar.textContent = config.notice;
    bar.hidden = false;
  }
  renderIdentity();
  document.getElementById("sign-out").addEventListener("click", async () => {
    try {
      const r = await postJSON(store.config.auth.logout_url);
      location.assign(r.redirect);
    } catch (e) { toast(e.message, "bad"); }
  });

  initDialogs(store);
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
    "collaboration": collaborationView(store),
  }, { container: view, nav: document.getElementById("nav") });
  router.start();
}

boot();
