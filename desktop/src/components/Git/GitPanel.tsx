import React, { useState } from "react";
import { useStore } from "../../store/session";
import { useWs } from "../../hooks/useWebSocket";

export default function GitPanel() {
  const diff = useStore((s) => s.diff);
  const connected = useStore((s) => s.connected);
  const { sendCommand } = useWs();
  const [undoing, setUndoing] = useState(false);

  const handleRefresh = () => sendCommand("diff");

  const handleUndo = () => {
    setUndoing(true);
    sendCommand("undo");
    setTimeout(() => {
      handleRefresh();
      setUndoing(false);
    }, 1000);
  };

  return (
    <div style={styles.container}>
      <div style={styles.header}>
        <span style={{ fontWeight: 600, fontSize: 14 }}>Git Diff Preview</span>
        <div style={{ display: "flex", gap: 8 }}>
          <button
            style={{ ...styles.btn, color: "#ff6666", borderColor: "#5a3a3a" }}
            onClick={handleUndo}
            disabled={undoing || !connected}
          >
            {undoing ? "..." : "Undo"}
          </button>
          <button style={styles.btn} onClick={handleRefresh}>Refresh</button>
        </div>
      </div>
      <div style={styles.body}>
        {!connected && (
          <div style={styles.empty}>Disconnected</div>
        )}
        {connected && !diff && (
          <div style={styles.empty}>No uncommitted changes. The diff will appear here when the agent modifies files.</div>
        )}
        {diff && (
          <pre style={styles.diff}>{diff}</pre>
        )}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: { display: "flex", flexDirection: "column", height: "100%", flex: 1 },
  header: {
    padding: "10px 20px", borderBottom: "1px solid #2a2a3e",
    display: "flex", justifyContent: "space-between", alignItems: "center",
    minHeight: 48,
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
  empty: {
    color: "#666", textAlign: "center" as const, padding: 40,
    fontFamily: "'Inter', sans-serif", fontSize: 13,
  },
};
