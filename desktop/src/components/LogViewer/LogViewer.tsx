import React, { useRef, useEffect } from "react";
import { useStore } from "../../store/session";
import { useWs } from "../../hooks/useWebSocket";

const LOG_COLORS: Record<string, string> = {
  run_start: "#7c7cff",
  llm_call: "#ffaa44",
  llm_response: "#44cc88",
  tool_call: "#66bbff",
  tool_result: "#888",
};

function formatTime(ts: number): string {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString("en-US", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export default function LogViewer() {
  const logs = useStore((s) => s.logs);
  const connected = useStore((s) => s.connected);
  const { sendCommand } = useWs();
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs.length]);

  const handleRefresh = () => {
    sendCommand("logs");
  };

  const handleClear = () => {
    sendCommand("logs:clear");
  };

  return (
    <div style={styles.container}>
      <div style={styles.header}>
        <span style={{ fontWeight: 600, fontSize: 14 }}>Agent Logs</span>
        <div style={{ display: "flex", gap: 8 }}>
          <button style={styles.btn} onClick={handleRefresh}>Refresh</button>
          <button style={styles.btn} onClick={handleClear}>Clear</button>
        </div>
      </div>
      <div style={styles.list}>
        {!connected && (
          <div style={styles.offline}>Disconnected — agent logs unavailable</div>
        )}
        {connected && logs.length === 0 && (
          <div style={styles.empty}>No logs yet. Send a chat message to see agent activity.</div>
        )}
        {logs.map((entry, i) => (
          <div key={i} style={styles.entry}>
            <span style={styles.time}>{formatTime(entry.timestamp as number)}</span>
            <span style={{ ...styles.badge, color: LOG_COLORS[entry.type] || "#aaa", borderColor: LOG_COLORS[entry.type] || "#aaa" }}>
              {entry.type}
            </span>
            {entry.iteration !== undefined && (
              <span style={styles.iter}>#{entry.iteration}</span>
            )}
            {entry.type === "llm_response" && (
              <span style={styles.msg}>{entry.content}</span>
            )}
            {entry.type === "tool_call" && (
              <span style={styles.msg}>
                {entry.name}
                <span style={styles.args}> {JSON.stringify(entry.args)}</span>
              </span>
            )}
            {entry.type === "tool_result" && (
              <span style={styles.msg}>{entry.result}</span>
            )}
            {entry.type === "run_start" && (
              <span style={styles.msg}>Run started</span>
            )}
            {entry.type === "llm_call" && (
              <span style={styles.msg}>Calling LLM...</span>
            )}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: { display: "flex", flexDirection: "column", height: "100%", flex: 1 },
  header: {
    padding: "10px 20px",
    borderBottom: "1px solid #2a2a3e",
    display: "flex", justifyContent: "space-between", alignItems: "center",
    minHeight: 48,
  },
  btn: {
    padding: "4px 14px",
    borderRadius: 6,
    border: "1px solid #3a3a5c",
    background: "#252540",
    color: "#bbb",
    cursor: "pointer",
    fontSize: 12,
  },
  list: {
    flex: 1, overflowY: "auto", padding: 8,
    fontFamily: "'JetBrains Mono', monospace", fontSize: 12,
  },
  entry: {
    display: "flex", alignItems: "center", gap: 8,
    padding: "3px 8px", borderRadius: 4,
    borderBottom: "1px solid #1e1e34",
  },
  time: { color: "#555", flexShrink: 0, fontSize: 11 },
  badge: {
    fontSize: 10, fontWeight: 600, letterSpacing: 0.5,
    padding: "1px 6px", borderRadius: 4, border: "1px solid",
    textTransform: "uppercase", flexShrink: 0,
  },
  iter: { color: "#666", fontSize: 10, flexShrink: 0 },
  msg: { color: "#ccc", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" as const },
  args: { color: "#888", fontSize: 11 },
  offline: { color: "#ff5555", padding: 20, textAlign: "center" as const, fontFamily: "'Inter', sans-serif", fontSize: 13 },
  empty: { color: "#666", padding: 20, textAlign: "center" as const, fontFamily: "'Inter', sans-serif", fontSize: 13 },
};
