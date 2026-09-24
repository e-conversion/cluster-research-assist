// Markdown -> sanitized HTML, plus a render-throttle for streaming text.

const md = window.marked;
const purify = window.DOMPurify;
if (md) md.setOptions({ gfm: true, breaks: false });
if (purify) {
  // external links open in a new tab and never leak the referrer
  purify.addHook("afterSanitizeAttributes", (node) => {
    if (node.tagName === "A" && node.getAttribute("href")) {
      node.setAttribute("target", "_blank");
      node.setAttribute("rel", "noopener noreferrer");
    }
  });
}

// No images: a model that has been steered by something it read could
// otherwise carry the conversation off in the URL of one.
const SANITIZE = { USE_PROFILES: { html: true }, FORBID_TAGS: ["img", "picture", "source"] };

export function render(text) {
  if (!purify) return escapeHtml(text || "");
  const raw = md ? md.parse(text || "") : escapeHtml(text || "").replace(/\n/g, "<br>");
  const fragment = purify.sanitize(raw, { ...SANITIZE, RETURN_DOM_FRAGMENT: true });
  linkDois(fragment);
  const box = document.createElement("div");
  box.append(fragment);
  return box.innerHTML;
}

// Crossref's pattern for modern DOIs. The answers cite papers by bare DOI, and
// marked only autolinks URLs, so the DOIs are linked here, after sanitizing:
// the anchors this adds carry a doi.org address built from the match, never
// anything the text supplied as a URL.
const DOI = /\b10\.\d{4,9}\/[^\s"<>]+/g;
// text already inside a link or shown as code stays as it is
const LEAVE_ALONE = new Set(["A", "CODE", "PRE"]);

/** A match without what ends the sentence around it: trailing punctuation,
 *  and a closing bracket the DOI itself never opened. */
function trimDoi(match) {
  let doi = match;
  for (;;) {
    const trimmed = doi.replace(/[.,;:!?'*_]+$/, "");
    const close = trimmed.at(-1);
    const open = { ")": "(", "]": "[" }[close];
    const unbalanced = open && trimmed.split(open).length < trimmed.split(close).length;
    const next = unbalanced ? trimmed.slice(0, -1) : trimmed;
    if (next === doi) return doi;
    doi = next;
  }
}

function doiLink(doi) {
  const a = document.createElement("a");
  a.href = "https://doi.org/" + doi.split("/").map(encodeURIComponent).join("/");
  a.textContent = doi;
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  return a;
}

function insideLeftAlone(node, root) {
  for (let p = node.parentNode; p && p !== root; p = p.parentNode) {
    if (LEAVE_ALONE.has(p.nodeName)) return true;
  }
  return false;
}

/** Turns every bare DOI in the text under ``root`` into a doi.org link. */
export function linkDois(root) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const found = [];
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (node.nodeValue.includes("10.") && !insideLeftAlone(node, root)) found.push(node);
  }
  for (const node of found) {
    const text = node.nodeValue;
    const parts = [];
    let last = 0;
    for (const match of text.matchAll(DOI)) {
      const doi = trimDoi(match[0]);
      parts.push(text.slice(last, match.index), doiLink(doi));
      last = match.index + doi.length;
    }
    if (!parts.length) continue;
    parts.push(text.slice(last));
    node.replaceWith(...parts.filter((part) => part !== ""));
  }
}

export function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/** Coalesces many small updates into one DOM write per animation frame. */
export class StreamRenderer {
  constructor(el, { cursor = true } = {}) {
    this.el = el;
    this.text = "";
    this.cursor = cursor;
    this._raf = 0;
    if (cursor) el.classList.add("cursor");
  }
  append(piece) {
    this.text += piece;
    if (!this._raf) this._raf = requestAnimationFrame(() => { this._raf = 0; this.el.innerHTML = render(this.text); });
  }
  finish(text) {
    if (this._raf) { cancelAnimationFrame(this._raf); this._raf = 0; }
    if (text != null) this.text = text;
    this.el.innerHTML = render(this.text);
    this.el.classList.remove("cursor");
  }
}
