// A Show/Hide control inside a password field, so a long or pasted password
// can be checked before it is sent. One helper for every such field, so they
// all behave alike. Only the input's type changes: its name and autocomplete
// stay, and a password manager fills it as before.

let labelIds = 0;

function show(button, input, on) {
  input.type = on ? "text" : "password";
  button.textContent = on ? "Hide" : "Show";
  button.setAttribute("aria-pressed", String(on));
}

/** Wraps a password input with its toggle and returns the wrapper, which takes
 *  the input's place in the page. */
export function revealable(input) {
  const wrap = document.createElement("span");
  wrap.className = "reveal";
  input.replaceWith(wrap);
  // shown as text, the field would be spellchecked (in Chrome possibly by a
  // server) and capitalised on a phone
  input.spellcheck = false;
  input.setAttribute("autocapitalize", "none");
  input.setAttribute("autocorrect", "off");
  const button = document.createElement("button");
  button.type = "button";
  button.className = "reveal-toggle";
  show(button, input, false);
  // keeps the caret, and a phone's keyboard, in the field
  button.addEventListener("mousedown", (e) => e.preventDefault());
  button.addEventListener("click", () => show(button, input, input.type === "password"));
  wrap.append(input, button);
  // inside the label, the button's word would become part of the field's name
  const caption = input.closest("label")?.querySelector(":scope > span");
  if (caption) {
    caption.id ||= `reveal-label-${++labelIds}`;
    input.setAttribute("aria-labelledby", caption.id);
  }
  return wrap;
}

/** Hides every revealed password under root: on submit, so the browser still
 *  sees a password field to offer saving, and when a dialog closes. */
export function conceal(root) {
  for (const button of root.querySelectorAll(".reveal-toggle[aria-pressed=true]")) {
    show(button, button.previousElementSibling, false);
  }
}
