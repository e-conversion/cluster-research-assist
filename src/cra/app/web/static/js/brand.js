// The deployment's logo, as the page shell draws it: a light and a dark
// drawing side by side, of which the stylesheet shows the one for the theme.
const version = document.documentElement.dataset.brandVersion || "";

/** shape is "square" or "wide"; the wide one is offered only when the brand has it. */
export function logo(shape = "square", cls = "") {
  const box = document.createElement("span");
  box.className = `logo ${shape === "wide" ? "wide" : ""} ${cls}`.trim();
  box.setAttribute("aria-hidden", "true");
  for (const tone of ["light", "dark"]) {
    const img = document.createElement("img");
    img.className = `on-${tone}`;
    img.alt = "";
    img.src = `brand/logo-${shape}-${tone}.svg?v=${version}`;
    box.append(img);
  }
  return box;
}
