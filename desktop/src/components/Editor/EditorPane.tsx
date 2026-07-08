import React, { useEffect, useState, useCallback } from "react";
import Editor from "@monaco-editor/react";
import { useWs } from "../../hooks/useWebSocket";
import { useStore } from "../../store/session";

/** Editor tab — file tree + Monaco with save over WS (file:read / file_write). */

const LANG_BY_EXT: Record<string, string> = {
  py: "python", ts: "typescript", tsx: "typescript", js: "javascript", jsx: "javascript",
  json: "json", md: "markdown", css: "css", html: "html", yml: "yaml", yaml: "yaml",
  toml: "ini", sh: "shell", rs: "rust", go: "go",
};

interface TreeNode { name: string; path: string; type: string; children?: TreeNode[] }

function Tree({ node, depth, onOpen, current }: { node: TreeNode; depth: number; onOpen: (p: string) => void; current: string }) {
  const [open, setOpen] = useState(depth < 1);
  const isDir = node.type === "directory" || !!node.children;
  return (
    <div>
      <div
        onClick={() => (isDir ? setOpen(!open) : onOpen(node.path))}
        style={{
          padding: "3px 6px", paddingLeft: 8 + depth * 14, cursor: "pointer", fontSize: 12.5,
          fontFamily: "'JetBrains Mono', monospace", whiteSpace: "nowrap", overflow: "hidden",
          textOverflow: "ellipsis", borderRadius: 6,
          color: node.path === current ? "#fff" : isDir ? "#9a9ab8" : "#c5c5da",
          background: node.path === current ? "#3b3b5c" : "transparent",
        }}
      >
        {isDir ? (open ? "▾ " : "▸ ") : "· "}{node.name}
      </div>
      {isDir && open && (node.children || []).map((c) => (
        <Tree key={c.path} node={c} depth={depth + 1} onOpen={onOpen} current={current} />
      ))}
    </div>
  );
}

export default function EditorPane() {
  const { sendCommand, subscribe, connected } = useWs();
  const theme = useStore((s) => s.theme);
  const [tree, setTree] = useState<TreeNode | null>(null);
  const [path, setPath] = useState("");
  const [content, setContent] = useState("");
  const [savedContent, setSavedContent] = useState("");
  const [status, setStatus] = useState("");

  useEffect(() => {
    const subs = [
      subscribe("files", (m: any) => setTree(m.data)),
      subscribe("file_content", (m: any) => {
        setPath(m.data.path);
        setContent(m.data.content);
        setSavedContent(m.data.content);
        setStatus("");
      }),
      subscribe("file_saved", (m: any) => {
        setSavedContent(content);
        setStatus(`saved ${m.data.path}`);
      }),
    ];
    return () => subs.forEach((u) => u());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subscribe, content]);

  useEffect(() => { if (connected) sendCommand("files"); }, [connected, sendCommand]);

  const openFile = useCallback((p: string) => sendCommand(`file:read:${p}`), [sendCommand]);
  const { sendRaw } = useWs();

  const dirty = content !== savedContent;
  const ext = path.split(".").pop() || "";

  return (
    <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>
      <div style={{ width: 240, overflow: "auto", borderRight: "1px solid #2a2a3e", padding: 8, flexShrink: 0 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "0 4px 8px" }}>
          <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: 1, color: "#7c7cff" }}>FILES</span>
          <button onClick={() => sendCommand("files")} style={btn}>↻</button>
        </div>
        {tree ? <Tree node={tree} depth={0} onOpen={openFile} current={path} />
              : <div style={{ fontSize: 12, color: "#666", padding: 8 }}>Loading…</div>}
      </div>
      <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
        <div style={{
          height: 36, display: "flex", alignItems: "center", justifyContent: "space-between",
          padding: "0 12px", borderBottom: "1px solid #2a2a3e", flexShrink: 0,
        }}>
          <span style={{ fontSize: 12.5, fontFamily: "'JetBrains Mono', monospace", color: "#c5c5da" }}>
            {path || "select a file"}{dirty ? " ●" : ""}
          </span>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <span style={{ fontSize: 11, color: "#5da672" }}>{status}</span>
            <button
              onClick={() => { if (path && sendRaw) { sendRaw({ type: "file_write", path, content }); } }}
              disabled={!dirty}
              style={{ ...btn, padding: "4px 14px", background: dirty ? "#7c7cff" : "#2a2a3e", color: dirty ? "#fff" : "#666" }}
            >
              Save
            </button>
          </div>
        </div>
        <div style={{ flex: 1, minHeight: 0 }}>
          {path ? (
            <Editor
              height="100%"
              language={LANG_BY_EXT[ext] || "plaintext"}
              value={content}
              theme={theme === "dark" ? "vs-dark" : "light"}
              onChange={(v) => setContent(v ?? "")}
              options={{ fontSize: 13, minimap: { enabled: false }, scrollBeyondLastLine: false, automaticLayout: true }}
            />
          ) : (
            <div style={{ display: "flex", height: "100%", alignItems: "center", justifyContent: "center", color: "#555", fontSize: 13 }}>
              Open a file from the tree — agent edits appear as reviewable changesets in Chat
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

const btn: React.CSSProperties = {
  padding: "3px 8px", borderRadius: 6, border: "1px solid #3a3a5c",
  background: "#252540", color: "#c0c0d8", cursor: "pointer", fontSize: 11,
};
