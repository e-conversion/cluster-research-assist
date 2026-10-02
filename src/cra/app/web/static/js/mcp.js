// The "Connect apps" tab of Settings: how to connect each app to the MCP
// endpoint, and the person's connected apps and tokens.
//
// The guides stay visible whether or not a token exists. Where the server lets
// apps sign in, no token is needed at all; otherwise the guides show where the
// token goes, with a placeholder until one is made, and with the new value
// until the dialog closes. Token labels are user input, so every row is built
// from elements and text, never from an HTML string.
import { getJSON, postJSON, del } from "./api.js";
import { toast } from "./toast.js";
import { copyText } from "./clipboard.js";

const APP_KEY = "cra.connect-app";
const APPS = ["claude", "chatgpt", "chatgpt-web", "other"];
const PLACEHOLDER = "<your token>";
const DOCS = {
  claude: "https://support.claude.com/en/articles/11175166-getting-started-with-custom-connectors-using-remote-mcp",
  claudeHeaders: "https://claude.com/docs/connectors/custom/add-unlisted#authenticate-with-request-headers",
  claudeDesktopConfig: "https://modelcontextprotocol.io/docs/develop/connect-local-servers",
  chatgpt: "https://developers.openai.com/api/docs/guides/developer-mode",
  claudeCode: "https://code.claude.com/docs/en/mcp",
  codex: "https://learn.chatgpt.com/docs/extend/mcp?surface=cli",
  cursor: "https://cursor.com/docs/context/mcp",
  vscode: "https://code.visualstudio.com/docs/agent-customization/mcp-servers",
  node: "https://nodejs.org/en/download",
};

const el = (id) => document.getElementById(id);
let store = null;
let minted = "";
let app = APPS[0];

// the server stores naive UTC timestamps
const parseUtc = (iso) => new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : iso + "Z");
const day = (iso) => (iso ? parseUtc(iso).toLocaleDateString(undefined, { dateStyle: "medium" }) : "—");
const moment = (iso) => (iso ? parseUtc(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "never");

function showError(id, message) {
  const p = el(id);
  p.textContent = message || "";
  p.hidden = !message;
}

// ---------- what the guides fill in ----------

const signIn = () => Boolean(store.config.mcp.sign_in);
const token = () => minted || PLACEHOLDER;
const title = () => store.config.cluster.display_name || store.config.cluster.name || "Library";

/** The endpoint as the server names it, which is the address a signing-in
 * app must use; failing that, as this page reached it. */
function mcpUrl() {
  return store.config.mcp.url || new URL(store.config.mcp.path, document.baseURI).href;
}

/** A key for the server in an app's configuration. */
function serverName() {
  return (store.config.cluster.name || "cluster").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "cluster";
}

// ---------- building blocks ----------

function h(tag, props, ...children) {
  const node = Object.assign(document.createElement(tag), props || {});
  node.append(...children.filter((c) => c !== null && c !== undefined && c !== false));
  return node;
}

const b = (text) => h("b", null, text);
const code = (text) => h("code", null, text);
const doc = (text, href) => h("a", { href, target: "_blank", rel: "noopener noreferrer", className: "doc-link" }, text, " ↗");

function copyButton(read, select) {
  const button = h("button", { type: "button", className: "btn", textContent: "Copy" });
  button.addEventListener("click", async () => {
    if (await copyText(read())) {
      toast("Copied");
    } else {
      select();
      toast("Select and copy it by hand", "bad");
    }
  });
  return button;
}

/** One value to type into an app, with its label as the app shows it. */
function field(label, value) {
  const input = h("input", { type: "text", readOnly: true, value, ariaLabel: label });
  return h("div", { className: "copy-field" },
    h("span", { className: "copy-label", textContent: label }),
    h("div", { className: "copy-row" }, input, copyButton(() => input.value, () => input.select())));
}

/** A command or a file's contents. */
function snippet(text) {
  const pre = h("pre", { className: "snippet", textContent: text });
  const select = () => {
    const range = document.createRange();
    range.selectNodeContents(pre);
    getSelection().removeAllRanges();
    getSelection().addRange(range);
  };
  return h("div", { className: "copy-row" }, pre, copyButton(() => pre.textContent, select));
}

const steps = (...items) => h("ol", { className: "steps" }, ...items.map((parts) => h("li", null, ...parts)));
const note = (...parts) => h("p", { className: "guide-note" }, ...parts);
const fold = (summary, ...body) => h("details", { className: "fold" }, h("summary", null, summary), h("div", { className: "fold-body" }, ...body));

function tokenStep() {
  return [minted ? "Your new token is filled in below." : "Create a token under ", minted ? null : b("Create a token"),
    minted ? null : " further down; it then appears in these instructions."];
}

// ---------- the guides ----------

function claudeGuide() {
  const open = ["On claude.ai or in the Claude desktop app, open ", b("Customize → Connectors"), ", click ", b("+ Add"),
    " and choose ", b("Add custom connector"), "."];
  const fill = ["Fill in the name and address, then click ", b("Continue"), ".",
    field("Name", title()), field("Remote MCP server URL", mcpUrl())];
  const team = note("On a Team or Enterprise plan only an owner can add it, under ", b("Organization settings → Connectors"),
    ". After that everyone finds it under ", b("Customize → Connectors"), " and clicks ", b("Connect"),
    signIn() ? ", signing in with their own account." : ".");
  if (signIn()) {
    return [
      note("Using Claude Code? See ", b("Other apps"), "."),
      steps(
        open,
        fill,
        ["Keep the suggested settings (", b("Sign in now"), ", ", b("Use Claude’s published identity"), ") and click ", b("Add"), "."],
        [`A ${title()} page opens: sign in and click `, b("Allow"), "."],
        ["In a chat, switch it on under ", b("+ → Connectors"), "."],
      ),
      note("Added once, it is part of your Claude account: Claude on the web, in the desktop app and on your phone, ",
        "and Claude Code when you are signed in to it with the same account."),
      team,
      doc("Claude’s guide to custom connectors", DOCS.claude),
    ];
  }
  return [
    note("Using Claude Code? See ", b("Other apps"), "."),
    steps(
      tokenStep(),
      open,
      fill,
      ["Under ", b("Authentication"), " choose ", b("No sign in"), ", add this request header, and click ", b("Add"), ".",
        field("Header", "authorization"), field("Value", `Bearer ${token()}`)],
      ["In a chat, switch it on under ", b("+ → Connectors"), "."],
    ),
    note("Request headers are a beta Claude offers to some organisations only. ", doc("Claude’s guide", DOCS.claudeHeaders)),
    fold("No “Request headers”? Use the desktop app’s configuration file",
      steps(
        ["Install ", doc("Node.js", DOCS.node), " (version 18 or newer)."],
        ["In the Claude desktop app open ", b("Settings → Developer"), " and click ", b("Edit Config"), ". It opens ",
          code("claude_desktop_config.json"), " (Windows: ", code("%APPDATA%\\Claude"), ", macOS: ",
          code("~/Library/Application Support/Claude"), ")."],
        ["Put this in the file, or add the inner entry to the ", code("mcpServers"), " already there:", snippet(desktopConfig())],
        ["Quit Claude completely (also from the system tray or menu bar) and start it again."],
      ),
      note("This works in the desktop app only, not on claude.ai or your phone. ", doc("How the configuration file works", DOCS.claudeDesktopConfig)),
    ),
    team,
  ];
}

function desktopConfig() {
  // the header value goes through env: the Windows app splits arguments at spaces
  return JSON.stringify({
    mcpServers: {
      [serverName()]: {
        command: "npx",
        args: ["-y", "mcp-remote", mcpUrl(), "--header", "Authorization:${AUTH_HEADER}"],
        env: { AUTH_HEADER: `Bearer ${token()}` },
      },
    },
  }, null, 2);
}

function noChatgpt() {
  const contact = store.config.auth?.contact || "the administrators";
  return [note("ChatGPT can only connect to servers that let it sign in, and this one does not yet. Ask ", contact,
    " to set it up; until then, Claude and the apps under ", b("Other apps"), " work.")];
}

function chatgptGuide() {
  if (!signIn()) return noChatgpt();
  return [
    note("Using Codex? See ", b("Other apps"), "."),
    steps(
      ["In the ChatGPT desktop app, open ", b("Integrations → Plugins"), ", click ", b("Add"), " and choose ", b("Add MCP server"), "."],
      ["Enter the name, switch the type from ", b("STDIO"), " to ", b("Streamable HTTP"), ", enter the URL, leave the other fields empty, and click ",
        b("Save"), ".", field("Name", serverName()), field("URL", mcpUrl())],
      ["Click ", b("Authenticate"), `. A ${title()} page opens: sign in and click `, b("Allow"), "."],
    ),
    note("The desktop app shares this connection with Codex on the command line and in your editor, but not with chatgpt.com. ",
      "It shows up below as “Codex”."),
  ];
}

function chatgptWebGuide() {
  if (!signIn()) return noChatgpt();
  return [
    note("Using Codex? See ", b("Other apps"), "."),
    steps(
      ["Open ", doc("chatgpt.com/plugins", "https://chatgpt.com/plugins"), ", click ", b("+"), " and choose ",
        b("Create custom MCP server"), "."],
      ["In the window that opens, click ", b("Create MCP App"), "."],
      ["Fill in the name and paste the address under ", b("Connection"), " (", b("Server URL"), "). Keep ",
        b("Authentication"), " on ", b("OAuth"), ".", field("Name", title()), field("Server URL", mcpUrl())],
      ["Tick ", b("I understand and want to continue"), " and click ", b("Create"), "."],
      [`A ${title()} page opens: sign in and click `, b("Allow"), "."],
    ),
    note("Custom MCP servers need a Plus, Pro, Business, Enterprise or Edu plan, and a workspace admin may have to allow them. ",
      "This connects ChatGPT on the web, not the desktop app: see ", b("ChatGPT app"), "."),
    doc("OpenAI’s guide to developer mode", DOCS.chatgpt),
  ];
}

function otherGuide() {
  const name = serverName();
  const url = mcpUrl();
  const header = `Authorization: Bearer ${token()}`;
  const sections = signIn()
    ? [
      fold("Claude Code",
        note("Already added it in Claude? If Claude Code is signed in with the same Claude account, it is there already: check with ",
          code("/mcp"), ". Otherwise:"),
        snippet(`claude mcp add --transport http --scope user ${name} ${url}\nclaude mcp login ${name}`),
        note(code("--scope user"), " makes it available in every project, not just the current folder, and in the desktop app’s ",
          b("Code"), " tab."),
        doc("Claude Code and MCP", DOCS.claudeCode)),
      fold("Codex",
        note("Already connected the ChatGPT desktop app? Codex shares its list, so it is there already. Otherwise, in a terminal:"),
        snippet(`codex mcp add ${name} --url ${url}\ncodex mcp login ${name}`),
        doc("Codex and MCP", DOCS.codex)),
      fold("Cursor",
        note("Add this to ", code("~/.cursor/mcp.json"), " and sign in when Cursor asks."),
        snippet(JSON.stringify({ mcpServers: { [name]: { url } } }, null, 2)),
        doc("Cursor and MCP", DOCS.cursor)),
      fold("VS Code",
        note("Run ", b("MCP: Add Server"), " from the command palette, give it this address, and sign in when VS Code asks."),
        field("URL", url),
        doc("VS Code and MCP", DOCS.vscode)),
    ]
    : [
      fold("Claude Code",
        snippet(`claude mcp add --transport http --scope user ${name} ${url} \\\n  --header "${header}"`),
        note(code("--scope user"), " makes it available in every project, not just the current folder."),
        doc("Claude Code and MCP", DOCS.claudeCode)),
      fold("Codex",
        note("Add this to ", code("~/.codex/config.toml"), ", which the command line, the app and the editor extension share:"),
        snippet(`[mcp_servers.${name}]\nurl = "${url}"\nhttp_headers = { Authorization = "Bearer ${token()}" }`),
        doc("Codex and MCP", DOCS.codex)),
      fold("Cursor",
        note("Add this to ", code("~/.cursor/mcp.json"), ":"),
        snippet(JSON.stringify({ mcpServers: { [name]: { url, headers: { Authorization: `Bearer ${token()}` } } } }, null, 2)),
        doc("Cursor and MCP", DOCS.cursor)),
      fold("VS Code",
        note("Run ", b("MCP: Open User Configuration"), " from the command palette and add:"),
        snippet(JSON.stringify({ servers: { [name]: { type: "http", url, headers: { Authorization: `Bearer ${token()}` } } } }, null, 2)),
        doc("VS Code and MCP", DOCS.vscode)),
    ];
  return [
    ...sections,
    fold("Any other app",
      note("Most apps take the server address, and either sign in themselves or need the token as a header:"),
      field("Server URL", url),
      field("Header", header),
      note(...tokenStep())),
    note("Apps keep separate lists. Claude shares its connectors with Claude Code when both use the same account; ",
      "the ChatGPT desktop app shares with Codex, but not with ChatGPT on the web."),
  ];
}

const GUIDES = { claude: claudeGuide, chatgpt: chatgptGuide, "chatgpt-web": chatgptWebGuide, other: otherGuide };

function renderGuide() {
  el("app-guide").replaceChildren(...GUIDES[app]());
  for (const button of el("app-seg").querySelectorAll("button[data-app]")) {
    const on = button.dataset.app === app;
    button.classList.toggle("on", on);
    button.setAttribute("aria-selected", String(on));
  }
}

function chooseApp(name) {
  if (!APPS.includes(name)) return;
  app = name;
  try { localStorage.setItem(APP_KEY, name); } catch { /* a remembered tab is a convenience */ }
  renderGuide();
}

// ---------- connected apps and tokens ----------

function tokenRow(row) {
  const tr = document.createElement("tr");
  if (row.state !== "active") tr.className = "ended";
  const label = h("td", null, row.label);
  if (row.signed_in) label.append(" ", h("span", { className: "badge", textContent: "signed in" }));
  tr.append(label, ...[day(row.created_at), day(row.expires_at), moment(row.last_used_at)].map((t) => h("td", null, t)));
  const action = document.createElement("td");
  if (row.state === "active") {
    const end = h("button", { type: "button", className: "btn danger ghost", textContent: row.signed_in ? "Disconnect" : "Revoke" });
    end.addEventListener("click", () => endToken(row, end));
    action.append(end);
  } else {
    action.append(h("span", { className: "badge", textContent: row.state }));
  }
  tr.append(action);
  return tr;
}

export async function loadTokens() {
  try {
    const { tokens } = await getJSON("api/tokens");
    el("token-rows").replaceChildren(...tokens.map(tokenRow));
    el("token-table").hidden = !tokens.length;
    el("token-empty").hidden = tokens.length > 0;
    showError("token-list-error", "");
  } catch (e) {
    showError("token-list-error", e.message);
  }
}

async function endToken(row, button) {
  button.disabled = true;
  try {
    await del(`api/tokens/${encodeURIComponent(row.id)}`);
    toast(row.signed_in ? `Disconnected “${row.label}”` : `Revoked “${row.label}”`);
    await loadTokens();
  } catch (e) {
    button.disabled = false;
    toast(e.message, "bad");
  }
}

async function createToken() {
  const label = el("token-label").value.trim();
  if (!label) { showError("token-error", "Give the token a label, so you know later which app holds it."); return; }
  showError("token-error", "");
  const button = el("token-create");
  button.disabled = true;
  try {
    const made = await postJSON("api/tokens", { label, days: Number(el("token-days").value) });
    minted = made.token;
    el("token-value").value = minted;
    el("token-minted").hidden = false;
    el("token-label").value = "";
    renderGuide();
    el("token-value").select();
    await loadTokens();
  } catch (e) {
    showError("token-error", e.message);
  } finally {
    button.disabled = false;
  }
}

/** The value must not linger in the page once the dialog is closed. */
export function forgetMinted() {
  const shown = Boolean(minted);
  minted = "";
  el("token-value").value = "";
  el("token-minted").hidden = true;
  showError("token-error", "");
  if (shown) renderGuide();
}

export function showMcp() {
  // tokens are the way in only for apps that cannot sign in
  el("token-why").textContent = signIn()
    ? "Only for an app that cannot sign in, or a script. Treat a token like a password: anyone who has it can search as you."
    : "Apps connect with a token. Give each app its own, so you can revoke one without the others, and treat it like a password.";
  renderGuide();
}

export function initMcp(s) {
  store = s;
  const mcp = store.config.mcp;
  if (!mcp) return;
  try { if (APPS.includes(localStorage.getItem(APP_KEY))) app = localStorage.getItem(APP_KEY); } catch { /* see chooseApp */ }
  for (const days of mcp.token_days.choices) {
    el("token-days").append(h("option", { value: String(days), textContent: `${days} days`, selected: days === mcp.token_days.default }));
  }
  el("app-seg").addEventListener("click", (e) => {
    const button = e.target.closest("button[data-app]");
    if (button) chooseApp(button.dataset.app);
  });
  el("token-create").addEventListener("click", createToken);
  el("token-label").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); createToken(); } });
  el("token-value-row").append(copyButton(() => el("token-value").value, () => el("token-value").select()));
}
