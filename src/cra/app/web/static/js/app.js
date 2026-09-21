// Bootstrap: load config + session, then start the router.
import { ApiError, getJSON, postJSON } from "./api.js";
import { createRouter } from "./router.js";
import { chatView } from "./chat.js";
import { initDialogs, toast } from "./settings.js";
import { corpusMapView } from "./views/corpus-map.js";
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
  return session;
}

function renderSignIn(view, config) {
  const page = document.createElement("div");
  page.className = "landing";
  page.innerHTML = `
    <div class="landing-head">
      <svg class="logo" aria-hidden="true"><use href="#logo-mark"/></svg>
      <h1>Sign in to ${config.title}</h1>
    </div>
    <p class="lede">The assistant answers questions about the research in ${config.cluster.name}.
    Sign in with your institutional account to start.</p>
    <p><a class="btn primary" href="${config.auth.login_url}">Sign in</a></p>`;
  view.replaceChildren(page);
  for (const id of ["nav", "new-chat", "menu"]) document.getElementById(id).hidden = true;
}

async function boot() {
  const view = document.getElementById("view");
  let config;
  try {
    config = await getJSON("api/config");
  } catch (e) {
    view.innerHTML = `<div class="page"><p class="note">Could not reach the server: ${e.message}</p></div>`;
    return;
  }
  document.title = config.title;
  document.querySelector(".brand-name").textContent = config.title;
  let session;
  try {
    session = await getJSON("api/session");
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) { renderSignIn(view, config); return; }
    view.innerHTML = `<div class="page"><p class="note">Could not reach the server: ${e.message}</p></div>`;
    return;
  }
  store.update({ config, session });
  const userLabel = document.getElementById("user-label");
  if (store.session.user) { userLabel.textContent = store.session.user; userLabel.hidden = false; }
  const signOut = document.getElementById("sign-out");
  signOut.hidden = false;
  signOut.addEventListener("click", async () => {
    try {
      const r = await postJSON(config.auth.logout_url);
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
    "corpus-map": corpusMapView(store),
    "collaboration": collaborationView(store),
  }, { container: view, nav: document.getElementById("nav") });
  router.start();
}

boot();
