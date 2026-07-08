// Hermes VS Code extension — thin client over the Hermes agent WebSocket server.
// Chat sidebar (streaming), native inline diffs via the changeset protocol,
// active-file context. Start the agent with: hermes ws   (or hermes web)
const vscode = require("vscode");
const path = require("path");

let WebSocketImpl;
try { WebSocketImpl = require("ws"); } catch { WebSocketImpl = globalThis.WebSocket; }

// ── WS client ─────────────────────────────────────────────────
class HermesClient {
  constructor() {
    this.ws = null;
    this.listeners = new Set();
    this.connected = false;
    this._timer = null;
  }
  url() {
    const cfg = vscode.workspace.getConfiguration("hermes");
    const host = cfg.get("host") || "ws://127.0.0.1:8765";
    const token = cfg.get("token") || "";
    return token ? `${host}/?token=${token}` : host;
  }
  connect() {
    if (this.ws && this.ws.readyState === 1) return;
    try {
      const ws = new WebSocketImpl(this.url());
      this.ws = ws;
      ws.on ? this._nodeWire(ws) : this._domWire(ws);
    } catch (e) { /* retry via timer */ }
    if (!this._timer) {
      this._timer = setInterval(() => {
        if (!this.ws || this.ws.readyState === 3) this.connect();
      }, 3000);
    }
  }
  _emit(msg) { this.listeners.forEach((fn) => { try { fn(msg); } catch {} }); }
  _nodeWire(ws) {
    ws.on("open", () => { this.connected = true; this._emit({ type: "_status", connected: true }); });
    ws.on("close", () => { this.connected = false; this._emit({ type: "_status", connected: false }); });
    ws.on("error", () => {});
    ws.on("message", (data) => { try { this._emit(JSON.parse(data.toString())); } catch {} });
  }
  _domWire(ws) {
    ws.onopen = () => { this.connected = true; this._emit({ type: "_status", connected: true }); };
    ws.onclose = () => { this.connected = false; this._emit({ type: "_status", connected: false }); };
    ws.onmessage = (ev) => { try { this._emit(JSON.parse(ev.data)); } catch {} };
    ws.onerror = () => {};
  }
  sendJson(obj) {
    if (this.ws && this.ws.readyState === 1) this.ws.send(JSON.stringify(obj));
    else vscode.window.showWarningMessage("Hermes agent not connected — run `hermes ws` first.");
  }
  chat(text) { this.sendJson({ type: "chat", text }); }
  command(cmd) { this.sendJson({ type: "command", command: cmd }); }
  onMessage(fn) { this.listeners.add(fn); return () => this.listeners.delete(fn); }
  dispose() { if (this._timer) clearInterval(this._timer); try { this.ws && this.ws.close(); } catch {} }
}

// ── original-content provider for native diffs ────────────────
const originals = new Map(); // "csId:path" -> old content
const pendingOld = new Map(); // same key -> resolve fn

class OrigProvider {
  provideTextDocumentContent(uri) {
    return originals.get(uri.path.replace(/^\//, "")) ?? "(original content unavailable)";
  }
}

async function showFileDiff(client, csId, relPath) {
  const key = `${csId}:${relPath}`;
  if (!originals.has(key)) {
    client.command(`changeset:old:${csId}:${relPath}`);
    await new Promise((resolve) => {
      pendingOld.set(key, resolve);
      setTimeout(resolve, 4000); // don't hang if the server never answers
    });
  }
  const ws = vscode.workspace.workspaceFolders?.[0];
  const fileUri = ws ? vscode.Uri.joinPath(ws.uri, relPath) : vscode.Uri.file(relPath);
  const origUri = vscode.Uri.parse(`hermes-orig:/${key}`);
  vscode.commands.executeCommand("vscode.diff", origUri, fileUri, `Hermes: ${relPath} (${csId})`);
}

function reviewChangeset(client, changeset) {
  for (const f of changeset.files || []) {
    vscode.window
      .showInformationMessage(`Hermes edited ${f.path}`, "Diff", "Accept", "Reject")
      .then((choice) => {
        if (choice === "Diff") showFileDiff(client, changeset.id, f.path);
        else if (choice === "Accept") client.command(`changeset:accept:${changeset.id}:${f.path}`);
        else if (choice === "Reject") client.command(`changeset:reject:${changeset.id}:${f.path}`);
      });
  }
}

// ── chat sidebar ──────────────────────────────────────────────
class ChatViewProvider {
  constructor(client) { this.client = client; this.view = null; }
  resolveWebviewView(view) {
    this.view = view;
    view.webview.options = { enableScripts: true };
    view.webview.html = CHAT_HTML;
    view.webview.onDidReceiveMessage((m) => {
      if (m.type === "send") this.client.chat(m.text);
      if (m.type === "cancel") this.client.command("cancel");
    });
  }
  post(msg) { this.view?.webview.postMessage(msg); }
}

const CHAT_HTML = `<!DOCTYPE html><html><head><style>
  body { font-family: var(--vscode-font-family); padding: 0 8px; display: flex; flex-direction: column; height: 96vh; }
  #log { flex: 1; overflow-y: auto; font-size: 12.5px; line-height: 1.5; }
  .u { color: var(--vscode-textLink-foreground); margin: 8px 0 2px; font-weight: 600; }
  .a { white-space: pre-wrap; word-break: break-word; }
  .meta { opacity: .55; font-size: 10.5px; margin: 2px 0 6px; }
  #row { display: flex; gap: 6px; padding: 8px 0; }
  textarea { flex: 1; resize: none; background: var(--vscode-input-background);
    color: var(--vscode-input-foreground); border: 1px solid var(--vscode-input-border);
    border-radius: 4px; padding: 6px; font-family: inherit; }
  button { background: var(--vscode-button-background); color: var(--vscode-button-foreground);
    border: none; border-radius: 4px; padding: 4px 12px; cursor: pointer; }
</style></head><body>
  <div id="log"><div class="meta">Hermes — start the agent with <b>hermes ws</b>, then ask anything.</div></div>
  <div id="row">
    <textarea id="in" rows="2" placeholder="Ask Hermes… (Enter to send)"></textarea>
    <button id="go">▶</button>
  </div>
<script>
  const vsapi = acquireVsCodeApi();
  const log = document.getElementById("log"), input = document.getElementById("in");
  let cur = null;
  function add(cls, text) { const d = document.createElement("div"); d.className = cls; d.textContent = text; log.appendChild(d); log.scrollTop = log.scrollHeight; return d; }
  function send() { const t = input.value.trim(); if (!t) return; add("u", "you ▸ " + t); cur = null; input.value = ""; vsapi.postMessage({ type: "send", text: t }); }
  document.getElementById("go").onclick = send;
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });
  window.addEventListener("message", (e) => {
    const m = e.data;
    if (m.type === "token") { if (!cur) cur = add("a", ""); cur.textContent += m.content; log.scrollTop = log.scrollHeight; }
    else if (m.type === "response") { if (cur) cur.textContent = m.content; else add("a", m.content || ""); add("meta", "routed → " + (m.routedTo || "?")); cur = null; }
    else if (m.type === "status") { add("meta", m.text); }
  });
</script></body></html>`;

// ── activation ────────────────────────────────────────────────
function activate(context) {
  const client = new HermesClient();
  const chat = new ChatViewProvider(client);
  const status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 90);
  status.text = "$(hubot) Hermes: …";
  status.show();

  client.onMessage((m) => {
    if (m.type === "_status") {
      status.text = m.connected ? "$(hubot) Hermes: on" : "$(hubot) Hermes: off";
      if (m.connected) chat.post({ type: "status", text: "connected" });
    } else if (m.type === "token") {
      chat.post({ type: "token", content: m.content });
    } else if (m.type === "response") {
      chat.post({ type: "response", content: m.content, routedTo: m.routedTo });
      if (m.routedTo) status.text = `$(hubot) Hermes: ${m.routedTo}`;
      if (m.changeset) reviewChangeset(client, m.changeset);
    } else if (m.type === "changeset_old" && m.data) {
      const key = `${m.data.id}:${m.data.path}`;
      originals.set(key, m.data.old ?? "(unavailable)");
      const resolve = pendingOld.get(key);
      if (resolve) { pendingOld.delete(key); resolve(); }
    } else if (m.type === "notification" && m.content) {
      chat.post({ type: "status", text: m.content });
    }
  });
  client.connect();

  context.subscriptions.push(
    status,
    { dispose: () => client.dispose() },
    vscode.window.registerWebviewViewProvider("hermes.chat", chat),
    vscode.workspace.registerTextDocumentContentProvider("hermes-orig", new OrigProvider()),
    vscode.commands.registerCommand("hermes.reconnect", () => client.connect()),
    vscode.commands.registerCommand("hermes.cancel", () => client.command("cancel")),
    vscode.commands.registerCommand("hermes.ask", async () => {
      const q = await vscode.window.showInputBox({ prompt: "Ask Hermes" });
      if (q) { chat.post({ type: "status", text: "you ▸ " + q }); client.chat(q); }
    }),
    vscode.commands.registerCommand("hermes.askAboutFile", async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) return vscode.window.showWarningMessage("No active file.");
      const rel = vscode.workspace.asRelativePath(editor.document.uri);
      const q = await vscode.window.showInputBox({ prompt: `Ask Hermes about ${rel}` });
      if (q) { chat.post({ type: "status", text: `you ▸ (@${rel}) ` + q }); client.chat(`@${rel} ${q}`); }
    })
  );
}

function deactivate() {}
module.exports = { activate, deactivate };
