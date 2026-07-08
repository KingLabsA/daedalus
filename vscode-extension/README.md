# Hermes for VS Code

Thin client for the Hermes Deep Mind agent: streaming chat sidebar, **native
inline diffs for every agent edit** (accept/reject via the changeset protocol),
active-file context, auto-routing across your local models.

## Setup

1. Start the agent (from your project directory):
   ```bash
   pip install hermes-ultimate
   hermes ws          # agent server on ws://127.0.0.1:8765
   ```
2. Install the extension:
   ```bash
   cd vscode-extension
   npm install
   npx @vscode/vsce package        # produces hermes-vscode-0.1.0.vsix
   code --install-extension hermes-vscode-0.1.0.vsix
   ```
   (Dev mode instead: open this folder in VS Code and press F5.)

3. Click the Hermes icon in the activity bar and chat.

## Features

- **Chat sidebar** — streamed tokens, shows which provider each answer routed to
- **Inline diffs** — when Hermes edits files, a notification per file offers
  **Diff** (VS Code's native diff of original vs current), **Accept**, **Reject**
  (reject restores the original content)
- **`Hermes: Ask About Current File`** — sends your question with `@<file>`
  attached (contents expanded server-side)
- **`Hermes: Cancel Current Run`** — stops a generation mid-stream

## Settings

- `hermes.host` — WS URL (default `ws://127.0.0.1:8765`)
- `hermes.token` — only needed when the server sets `HERMES_WS_TOKEN`
  (`hermes web` prints its token; `hermes ws` runs open on localhost)
