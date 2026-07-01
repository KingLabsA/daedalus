import React from "react";
import { useStore } from "../../store/session";
import { useWs } from "../../hooks/useWebSocket";

export default function LspPanel() {
  const lsp = useStore((s) => s.lsp);
  const connected = useStore((s) => s.connected);
  const { sendCommand } = useWs();

  const handleRefresh = () => sendCommand("lsp");

  return (
    <div style={styles.container}>
      <div style={styles.header}>
        <span style={{ fontWeight: 600, fontSize: 14 }}>LSP Diagnostics</span>
        <div style={{ display: "flex", gap: 8 }}>
          <button style={styles.btn} onClick={handleRefresh}>Refresh</button>
        </div>
      </div>
      <div style={styles.body}>
        {!connected && (
          <div style={styles.empty}>Disconnected</div>
        )}
        {connected && !lsp && (
          <div style={styles.empty}>No diagnostics. Run pyright diagnostics to check for errors.</div>
        )}
        {lsp && lsp.includes("pyright not installed") && (
          <div style={styles.empty}>
            pyright not installed. Run: <code style={{ color: "#ffaa44" }}>npm install -g pyright</code>
          </div>
        )}
        {lsp && !lsp.includes("pyright not installed") && (
          <pre style={styles.output}>{lsp}</pre>
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
  output: {
    fontFamily: "'JetBrains Mono', monospace", fontSize: 12, lineHeight: 1.5,
    color: "#ccc", whiteSpace: "pre-wrap", margin: 0,
  },
  empty: {
    color: "#666", textAlign: "center" as const, padding: 40,
    fontFamily: "'Inter', sans-serif", fontSize: 13,
  },
};
