// What the two map views share: loading their drawing library, and the
// button that lets the frame take the whole window below the header.

const loading = new Map();

/** A pinned CDN build, loaded once; resolves when `global` is defined. */
export function loadScript(url, integrity, global) {
  if (window[global]) return Promise.resolve();
  if (!loading.has(url)) loading.set(url, new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = url; s.integrity = integrity; s.crossOrigin = "anonymous";
    s.onload = resolve;
    s.onerror = () => { loading.delete(url); reject(new Error(`${global} failed to load`)); };
    document.head.append(s);
  }));
  return loading.get(url);
}

/** Add the full-window toggle to a .viz-frame; returns the cleanup. */
export function expandable(frame) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "viz-expand";
  button.innerHTML = '<svg class="ico on-collapsed"><use href="#i-expand"/></svg><svg class="ico on-expanded"><use href="#i-collapse"/></svg>';
  const set = (on) => {
    frame.classList.toggle("expanded", on);
    const label = on ? "Exit full window" : "Full window";
    button.title = label;
    button.setAttribute("aria-label", label);
    button.setAttribute("aria-pressed", String(on));
  };
  button.addEventListener("click", () => set(!frame.classList.contains("expanded")));
  const onKey = (e) => { if (e.key === "Escape" && frame.classList.contains("expanded")) set(false); };
  addEventListener("keydown", onKey);
  set(false);
  frame.append(button);
  return () => removeEventListener("keydown", onKey);
}
