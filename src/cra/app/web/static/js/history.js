// Past conversations: the panel behind the clock icon in the header.
import { del, getJSON, postJSON } from "./api.js";

const $ = (id) => document.getElementById(id);

function when(iso) {
  const at = new Date(iso);
  const days = (Date.now() - at.getTime()) / 86_400_000;
  if (days < 1) return at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (days < 7) return at.toLocaleDateString([], { weekday: "short" });
  return at.toLocaleDateString([], { day: "numeric", month: "short" });
}

function icon(symbol, title, onClick) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "icon-btn";
  b.title = title;
  b.setAttribute("aria-label", title);
  b.innerHTML = `<svg class="ico"><use href="#${symbol}"/></svg>`;
  b.addEventListener("click", (e) => { e.stopPropagation(); onClick(); });
  return b;
}

export function initHistory(store, { onOpen, toast }) {
  const panel = $("history");
  const list = $("history-list");

  async function refresh() {
    let conversations = [];
    try {
      ({ conversations } = await getJSON("api/conversations"));
    } catch (e) {
      toast(e.message, "bad");
      return;
    }
    list.replaceChildren();
    if (!conversations.length) {
      const empty = document.createElement("p");
      empty.className = "hint";
      empty.textContent = "Nothing yet. Ask something and it appears here.";
      list.append(empty);
      return;
    }
    for (const c of conversations) {
      const row = document.createElement("div");
      row.className = "history-item" + (c.current ? " on" : "");

      const open = document.createElement("button");
      open.type = "button";
      open.className = "open";
      open.textContent = c.title;
      open.title = c.title;
      open.addEventListener("click", async () => {
        try {
          const opened = await postJSON(`api/conversations/${c.id}/open`);
          close();
          await onOpen(opened);
        } catch (e) { toast(e.message, "bad"); }
      });

      const stamp = document.createElement("span");
      stamp.className = "when";
      stamp.textContent = when(c.updated_at);

      row.append(open, stamp);
      row.append(
        icon("i-modify", "Rename", async () => {
          const title = prompt("Name this conversation", c.title);
          if (!title) return;
          try {
            await fetch(`api/conversations/${c.id}`, {
              method: "PUT",
              headers: { "content-type": "application/json" },
              body: JSON.stringify({ title }),
            });
            await refresh();
          } catch (e) { toast(e.message, "bad"); }
        }),
        icon("i-trash", "Delete", async () => {
          if (!confirm(`Delete "${c.title}"?`)) return;
          try {
            await del(`api/conversations/${c.id}`);
            await refresh();
            if (c.current) await onOpen({ conversation: null, messages: [] });
          } catch (e) { toast(e.message, "bad"); }
        }),
      );
      list.append(row);
    }
  }

  function close() { panel.hidden = true; }

  $("history-btn").addEventListener("click", async () => {
    panel.hidden = !panel.hidden;
    if (!panel.hidden) await refresh();
  });
  $("history-close").addEventListener("click", close);
  document.addEventListener("click", (e) => {
    if (!panel.hidden && !panel.contains(e.target) && !$("history-btn").contains(e.target)) close();
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
  addEventListener("routechange", (e) => { if (e.detail.name !== "chat") close(); });

  return { refresh };
}
