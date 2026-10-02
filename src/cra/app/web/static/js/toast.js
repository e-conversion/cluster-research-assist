// Brief messages, bottom right. Its own module so a view can report a failure
// without importing the settings panel, which imports the app, which imports
// the views.
//
// The stack is a manual popover: a modal dialog sits in the top layer with a
// blurring backdrop, and only another top-layer element can show above it.
// Re-showing the popover moves it to the top of that layer, above any dialog
// opened since.
function raise(box) {
  if (box.matches(":popover-open")) box.hidePopover();
  box.showPopover();
}

export function toast(message, kind = "") {
  const box = document.getElementById("toasts");
  const t = document.createElement("div");
  t.className = "toast " + kind;
  t.textContent = message;
  // re-showing the stack would replay the entrance of toasts already shown
  t.addEventListener("animationend", () => t.classList.add("settled"), { once: true });
  box.append(t);
  raise(box);
  setTimeout(() => {
    t.remove();
    if (!box.childElementCount) box.hidePopover();
  }, 3200);
}
