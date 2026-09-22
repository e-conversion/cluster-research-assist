// Light, dark, or follow the system. Each page applies the stored choice in a
// small inline script before first paint; this module keeps the controls in
// sync and writes the choice back.

export const THEME_KEY = "econverse-theme";
const MODES = ["system", "light", "dark"];

export function currentTheme() {
  try {
    const mode = localStorage.getItem(THEME_KEY);
    return MODES.includes(mode) ? mode : "system";
  } catch {
    return "system"; // private mode
  }
}

export function applyTheme(mode) {
  const root = document.documentElement;
  if (mode === "light" || mode === "dark") root.dataset.theme = mode;
  else delete root.dataset.theme;
  try {
    localStorage.setItem(THEME_KEY, mode);
  } catch { /* private mode */ }
  syncControls(mode);
  for (const frame of document.querySelectorAll("iframe")) propagateTheme(frame);
}

/** Mark the active button in every theme control on the page. */
export function syncControls(mode = currentTheme()) {
  for (const b of document.querySelectorAll(".seg button[data-theme]")) {
    b.classList.toggle("on", b.dataset.theme === mode);
  }
}

/** Same-origin iframes (the maps) carry their own stylesheet; hand them the choice. */
export function propagateTheme(iframe) {
  try {
    const root = iframe.contentDocument?.documentElement;
    if (!root) return;
    const mode = currentTheme();
    if (mode === "light" || mode === "dark") root.dataset.theme = mode;
    else delete root.dataset.theme;
  } catch { /* cross-origin */ }
}

/** A System / Light / Dark control, wired to the stored preference. */
export function themeControl(label = "Appearance") {
  const seg = document.createElement("div");
  seg.className = "seg";
  seg.setAttribute("role", "radiogroup");
  seg.setAttribute("aria-label", label);
  for (const mode of MODES) {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.theme = mode;
    button.textContent = mode[0].toUpperCase() + mode.slice(1);
    seg.append(button);
  }
  seg.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-theme]");
    if (button) applyTheme(button.dataset.theme);
  });
  syncControls();
  return seg;
}
