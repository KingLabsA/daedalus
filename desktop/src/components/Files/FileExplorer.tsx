import React, { useEffect, useState } from "react";
import { useStore, FileNode } from "../../store/session";
import { useWs } from "../../hooks/useWebSocket";

function FileTreeItem({ node, depth = 0 }: { node: FileNode; depth?: number }) {
  const [open, setOpen] = useState(depth < 2);
  const isDir = node.type === "dir";
  const icon = isDir ? (open ? "▾" : "▸") : "◦";
  const sizeStr = !isDir && node.size ? ` (${node.size > 1024 ? `${(node.size/1024).toFixed(1)}K` : `${node.size}B`})` : "";

  return (
    <div>
      <div
        style={{ paddingLeft: depth * 16, cursor: isDir ? "pointer" : "default", padding: "2px 8px", fontSize: 13, color: isDir ? "#7c7cff" : "#ccc", fontFamily: "'JetBrains Mono', monospace" }}
        onClick={() => isDir && setOpen(!open)}
      >
        <span style={{ marginRight: 6, fontSize: 11 }}>{icon}</span>
        {node.name}
        <span style={{ color: "#555", fontSize: 11 }}>{sizeStr}</span>
      </div>
      {isDir && open && node.children?.map((child) => (
        <FileTreeItem key={child.name} node={child} depth={depth + 1} />
      ))}
    </div>
  );
}

export default function FileExplorer() {
  const files = useStore((s) => s.files);
  const connected = useStore((s) => s.connected);
  const { sendCommand } = useWs();
  const [path, setPath] = useState(".");

  useEffect(() => {
    if (connected) sendCommand("files");
  }, [connected, sendCommand]);

  const handleRefresh = () => sendCommand(path === "." ? "files" : `files:${path}`);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div style={{ padding: "10px 16px", borderBottom: "1px solid #2a2a3e", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ fontWeight: 600, fontSize: 14 }}>Files</span>
        <button onClick={handleRefresh} style={{ background: "transparent", border: "1px solid #3a3a5c", color: "#7c7cff", borderRadius: 6, padding: "3px 10px", fontSize: 12, cursor: "pointer" }}>
          Refresh
        </button>
      </div>
      <div style={{ padding: "6px 16px", borderBottom: "1px solid #2a2a3e" }}>
        <input
          value={path}
          onChange={(e) => setPath(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleRefresh()}
          style={{ width: "100%", padding: "6px 10px", borderRadius: 6, border: "1px solid #3a3a5c", background: "#1e1e36", color: "#e0e0e0", fontSize: 12, outline: "none", fontFamily: "'JetBrains Mono', monospace" }}
          placeholder="Path..."
        />
      </div>
      <div style={{ flex: 1, overflowY: "auto", padding: "4px 0" }}>
        {files ? (
          <FileTreeItem node={files} />
        ) : (
          <div style={{ padding: 16, color: "#555", fontSize: 13, textAlign: "center" }}>
            {connected ? "Loading..." : "Agent offline"}
          </div>
        )}
      </div>
    </div>
  );
}
