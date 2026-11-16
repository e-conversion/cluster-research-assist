// What the service offers, in the words of the deployment (the
// service_description setting). Anyone may read it: it sits beside the sign-in
// form, and #/about is a stable address for it, signed in or not. An admin
// writes it in Markdown, sanitized as the chat's answers are.
import { render } from "./markdown.js";

export const ABOUT = "#/about";

export function isAbout() {
  return location.hash.split("?")[0] === ABOUT;
}

/** The description as a block of rendered Markdown, or null when there is none. */
export function aboutText(config) {
  if (!config.about) return null;
  const box = document.createElement("div");
  box.className = "md about";
  box.innerHTML = render(config.about);
  return box;
}

/** The route a signed-in person reaches from the menu. */
export function aboutView(store) {
  return {
    mount(container) {
      const page = document.createElement("div");
      page.className = "page about-page";
      const title = document.createElement("h1");
      title.textContent = store.config.title;
      page.append(title);
      const text = aboutText(store.config);
      if (text) page.append(text);
      else {
        const none = document.createElement("p");
        none.className = "muted";
        none.textContent = "This service has not described itself yet.";
        page.append(none);
      }
      container.replaceChildren(page);
    },
  };
}
