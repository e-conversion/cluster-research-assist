// Light, dark, or follow the system. theme-boot.js applies the stored choice
// before first paint; this module keeps the controls in sync and writes the
// choice back.

export const THEME_KEY = "cra-theme";
const MODES = ["system", "light", "dark"];

export function currentTheme() {
  try {
    const mode = localStorage.getItem(THEME_KEY);
    return MODES.includes(mode) ? mode : "system";
  } catch {
    return "system"; // private mode
  }
}

/** The theme a choice comes to: "system" resolves to what the system shows now. */
export function effectiveTheme(mode = currentTheme()) {
  if (mode === "light" || mode === "dark") return mode;
  return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function applyTheme(mode) {
  try {
    localStorage.setItem(THEME_KEY, mode);
  } catch { /* private mode */ }
  document.documentElement.dataset.theme = effectiveTheme(mode);
  syncControls(mode);
  for (const frame of document.querySelectorAll("iframe")) propagateTheme(frame);
}

/** Mark the active button in every theme control on the page. */
export function syncControls(mode = currentTheme()) {
  for (const b of document.querySelectorAll(".seg button[data-theme]")) {
    b.classList.toggle("on", b.dataset.theme === mode);
  }
}

/** Same-origin iframes (the maps) run their own theme-boot; hand them a change made here. */
export function propagateTheme(iframe) {
  try {
    const root = iframe.contentDocument?.documentElement;
    if (root) root.dataset.theme = effectiveTheme();
  } catch { /* cross-origin */ }
}
