# Phase 13 — Code Intelligence Upgrade: LSP Client + Embedding Search (roadmap Phase 3)

**Date:** 2026-07-04

## Goal
Competitive code intelligence on large repos: precise go-to-definition/references/
diagnostics via real language servers, and semantic search that scales past TF-IDF —
local-first (Ollama embeddings), zero new hard dependencies, graceful fallbacks.

## Components (`core/intel/`, standalone, stdlib+requests only)

**`lsp.py` — LspClient.** Minimal Language Server Protocol client, stdio transport
(Content-Length framed JSON-RPC 2.0):
- Server registry by extension: `.py` → `pyright-langserver --stdio`,
  `.ts/.tsx/.js/.jsx` → `typescript-language-server --stdio`; availability via
  `shutil.which`, graceful "not installed (npm i -g …)" strings.
- Per-server reader thread: responses → queue; `textDocument/publishDiagnostics`
  notifications → per-URI store; server→client *requests* (pyright sends
  `workspace/configuration` etc.) answered with null/empty so the server doesn't hang.
- `initialize` handshake (rootUri = cwd), `didOpen` before queries.
- API: `definition(path, line, char)`, `references(path, line, char)`,
  `diagnostics(path, timeout)` — results normalized to `{file, line, character}`.
  Connections cached; `close_all()`. Never raises into the loop.

**`embeddings.py` — EmbeddingIndex + HybridSearch.**
- `embed_fn` injectable; default = Ollama `/api/embeddings`
  (`HERMES_EMBED_MODEL`, default `nomic-embed-text`), availability probed.
- Chunks files like SemanticIndex (~40 lines), embeds (capped files/chunks),
  cosine top-k. In-memory, lazy build; per-file content-hash cache in
  `.hermes/embed_cache.json` so rebuilds only re-embed changed files.
- `HybridSearch(embedding, tfidf)`: uses embeddings when the embed model answers,
  else falls back to the existing TF-IDF index — same result shape.

## Wiring
- Tools: `goto_definition(filepath, line, character)`, `find_usages(filepath,
  line, character)` (LSP-precise), `lsp_diagnostics` upgraded to the live client
  (falls back to old pyright-CLI path); `semantic_search` → HybridSearch.
- Doctor: report which language servers are installed.

## Testing
Offline: fake LSP server script (Content-Length framing; answers initialize/
definition/references; emits publishDiagnostics; issues a server→client request
to prove we reply) — mirrors the MCP fake-server pattern. Embeddings with fake
embed_fn (relevance ordering, hash-cache reuse, fallback to TF-IDF when embed
unavailable). Suite stays green.

## Out of scope
Hover/completion/rename via LSP; wiring LSP into blast-radius (later); VS Code ext (roadmap P4).
