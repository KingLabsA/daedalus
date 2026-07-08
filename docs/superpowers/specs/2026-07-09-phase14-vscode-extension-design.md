# Phase 14 — VS Code Extension (roadmap Phase 4)

**Date:** 2026-07-09

## Goal
Meet users in their editor: a thin VS Code client over the existing Hermes WS
server — chat sidebar with streaming, native inline diffs driven by the
changeset protocol, active-file context. No engine changes; one tiny WS addition.

## Design (`vscode-extension/`, plain JS, single dependency: `ws`)

**Connection.** Extension host owns one WebSocket to `hermes.host`
(default `ws://127.0.0.1:8765`, optional `hermes.token` setting appended as
`?token=`); auto-reconnect every 3 s; status bar item shows connection state
and which provider the last answer routed to.

**Chat sidebar.** Webview view in its own activity-bar container. The webview
is dumb UI (messages pane + input); the extension host bridges WS↔webview via
postMessage: `token` chunks stream live, `response` finalizes with routedTo.

**Inline diffs (native).** When a response carries a `changeset`, a
notification per file offers **Diff / Accept / Reject**:
- Diff: fetch original bytes via new WS command `changeset:old:<id>:<path>`
  (backend addition: `ChangesetManager.original()`), serve it through a
  `hermes-orig:` TextDocumentContentProvider, and open VS Code's built-in
  `vscode.diff` against the on-disk file — real editor diff UI, zero custom
  rendering.
- Accept/Reject: send the existing `changeset:accept/reject` commands.

**Active-file context.** Command `Hermes: Ask about current file` prefixes the
question with an `@<relative-path>` mention (server-side expansion already
exists). `Hermes: Cancel` sends the cancel command.

**Packaging.** `npm install && npx @vscode/vsce package` → install the .vsix;
or open the folder and F5 for dev mode. Not published to the marketplace yet
(needs a publisher account) — documented in the extension README.

## Testing
Backend: unit test for `ChangesetManager.original()` + WS command. Extension:
`node --check extension.js`, JSON validity of manifest (headless VS Code UI
testing is out of scope for MVP).

## Out of scope
Marketplace publish, completion/hover, per-hunk UI inside VS Code (whole-file
accept/reject first; hunks remain available in the Web IDE), settings UI.
