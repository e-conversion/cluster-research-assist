// The consent page of the MCP sign-in. The answer goes as JSON, like every
// state-changing call here; the server replies with where the app expects the
// person back, and the page goes there.
import { postJSON } from "./api.js";

const box = document.getElementById("consent");
const error = document.getElementById("consent-error");

box.addEventListener("click", async (e) => {
  const button = e.target.closest("button[data-approve]");
  if (!button) return;
  for (const b of box.querySelectorAll("button")) b.disabled = true;
  error.hidden = true;
  try {
    const { redirect } = await postJSON("consent/answer", {
      request: box.dataset.request,
      approve: button.dataset.approve === "true",
    });
    location.assign(redirect);
  } catch (err) {
    error.textContent = err.message;
    error.hidden = false;
    for (const b of box.querySelectorAll("button")) b.disabled = false;
  }
});
