import React, { useState, useEffect } from "react";
import { useStore } from "../../store/session";
import { useWs } from "../../hooks/useWebSocket";

type GitView = "diff" | "branches" | "log";

export default function GitPanel() {
  const diff = useStore((s) => s.diff);
  const gitBranches = useStore((s) => s.gitBranches);
  const gitLog = useStore((s) => s.gitLog);
  const connected = useStore((s) => s.connected);
  const { sendCommand } = useWs();
  const [view, setView] = useState<GitView>("diff");
  const [undoing, setUndoing] = useState(false);

  useEffect(() => {
    if (connected) {
      sendCommand("diff");
      sendCommand("git_branches");
      sendCommand("git_log");
    }
  }, [connected, sendCommand]);

  const handleRefresh = () => {
    if (view === "diff") sendCommand("diff");
    else if (view === "branches") sendCommand("git_branches");
    else sendCommand("git_log");
  };

  const handleUndo = () => {
    setUndoing(true);
    sendCommand("undo");
    setTimeout(() => { handleRefresh(); setUndoing(false); }, 1000);
  };

  const handlePush = () => sendCommand("git_push");

  const content = view === "diff" ? diff : view === "branches" ? gitBranches : gitLog;

  return (
    <div style={styles.container}>
      <div style={styles.header}>
        <div style={{ display: "flex", gap: 4 }}>
          {(["diff", "branches", "log"] as GitView[]).map((v) => (
            <button key={v} onClick={() => { setView(v); setTimeout(handleRefresh, 100); }}
              style={{ ...styles.tabBtn, background: view === v ? "#2d2d3d" : "transparent", color: view === v ? "#7c7cff" : "#888" }}>
              {v === "diff" ? "Diff" : v === "branches" ? "Branches" : "Log"}
            </button>
          ))}
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          <button style={{ ...styles.btn, color: "#44cc88", borderColor: "#3a5a3a" }} onClick={handlePush} disabled={!connected}>Push</button>
          <button style={{ ...styles.btn, color: "#ff6666", borderColor: "#5a3a3a" }} onClick={handleUndo} disabled={undoing || !connected}>
            {undoing ? "..." : "Undo"}
          </button>
          <button style={styles.btn} onClick={handleRefresh}>Refresh</button>
        </div>
      </div>
      <div style={styles.body}>
        {!connected && <div style={styles.empty}>Disconnected</div>}
        {connected && !content && <div style={styles.empty}>No data. Click Refresh.</div>}
        {content && <pre style={styles.diff}>{content}</pre>}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: { display: "flex", flexDirection: "column", height: "100%", flex: 1 },
  header: {
    padding: "10px 20px", borderBottom: "1px solid #2a2a3e",
    display: "flex", justifyContent: "space-between", alignItems: "center", minHeight: 48,
  },
  tabBtn: {
    padding: "4px 12px", borderRadius: 6, border: "1px solid #3a3a5c",
    background: "transparent", cursor: "pointer", fontSize: 12, fontWeight: 500,
  },
  btn: {
    padding: "4px 14px", borderRadius: 6, border: "1px solid #3a3a5c",
    background: "#252540", color: "#bbb", cursor: "pointer", fontSize: 12,
  },
  body: { flex: 1, overflow: "auto", padding: 16 },
  diff: {
    fontFamily: "'JetBrains Mono', monospace", fontSize: 12, lineHeight: 1.5,
    color: "#ccc", whiteSpace: "pre-wrap", margin: 0,
  },
  empty: { color: "#666", textAlign: "center" as const, padding: 40, fontSize: 13 },
};
