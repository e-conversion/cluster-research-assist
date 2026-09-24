// Brief messages, bottom right. Its own module so a view can report a failure
// without importing the settings panel, which imports the app, which imports
// the views.
export function toast(message, kind = "") {
  const t = document.createElement("div");
  t.className = "toast " + kind;
  t.textContent = message;
  document.getElementById("toasts").append(t);
  setTimeout(() => t.remove(), 3200);
}
