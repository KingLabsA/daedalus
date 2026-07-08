# Phase 12 — Web IDE: Editor Pane + Changeset Review (roadmap Phase 1)

**Date:** 2026-07-04

## Goal
Make the Web IDE a true IDE: view/edit files in-browser, and review every agent
edit as a changeset with accept/revert — without breaking the agent's ability to
verify its own work.

## Design (lean; engine untouched except hooks)

**Apply-then-review, not defer.** Deferring writes would break the agent's
test-run verification mid-turn. Instead, destructive file tools apply
immediately (as today) while a **ChangesetManager** records old/new content per
file per turn. Review UI shows diffs after the turn; **Reject = restore old
content** (safe: we hold the original), Accept = keep.

**`core/changeset.py` — ChangesetManager** (standalone, stdlib-only):
- Hooks: `pre_tool` caches (path, old content) for write_file/append_file/
  edit_file_line; `post_tool` (non-error) reads new content, stores entry
  `{path, old, new, diff, status: applied}`.
- `begin_turn()` (called from `converse()`) groups entries; keeps last 20 turns.
- `summary(cs_id)` → files + unified diffs; `reject(cs_id, path)` restores old
  (status reverted); `accept(cs_id, path)` marks accepted. Never raises.

**WS protocol:**
- Chat response gains `changeset: {id, files: [{path, status, diff}]}`.
- Commands: `changeset:list`, `changeset:accept:<id>:<path>`,
  `changeset:reject:<id>:<path>`, `file:read:<path>` → `{type:"file_content"}`.
- New message type `{"type":"file_write","path","content"}` (user edits from
  the editor pane; path-traversal guarded to cwd).

**Frontend:**
- **Editor tab**: file tree (existing `files` command) + Monaco
  (`@monaco-editor/react`) with save (file_write) and dirty indicator.
- **Changeset review**: panel rendered under each chat answer when the response
  carries a changeset — per-file diff (collapsible) with Accept / Reject
  buttons wired to the WS commands; status badges update on reply.

## Testing
`tests/test_changeset.py`: record on write tools, no record on read/error,
reject restores bytes, accept marks, turn grouping + cap, diff rendering,
path guard for file_write helper. Frontend: tsc + vite build.

## Out of scope (later roadmap phases)
Per-hunk granularity (week-4 follow-up), VS Code extension, LSP, embeddings.
