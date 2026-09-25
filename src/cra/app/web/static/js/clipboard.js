// The Clipboard API exists only in secure contexts (https, localhost); served
// over plain http under any other name, the older copy command still works.
export async function copyText(text) {
  if (navigator.clipboard) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch { /* fall through */ }
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.opacity = "0";
  // outside an open modal dialog everything is inert and cannot be selected
  (document.activeElement?.closest("dialog[open]") || document.body).append(area);
  area.select();
  try {
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    area.remove();
  }
}
