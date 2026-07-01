import React, { useState } from "react";
import { useStore } from "../../store/session";
import { useWs } from "../../hooks/useWebSocket";

export default function SessionPanel() {
  const sessions = useStore((s) => s.sessions);
  const messages = useStore((s) => s.messages);
  const { sendCommand, subscribe } = useWs();
  const [loading, setLoading] = useState(false);
  const [msg, setMsg] = useState("");

  React.useEffect(() => {
    const unsub = subscribe("sessions", (data: unknown) => {
      useStore.getState().setSessions((data as any).data || []);
    });
    return unsub;
  }, [subscribe]);

  const handleRefresh = () => {
    setLoading(true);
    sendCommand("sessions");
    setTimeout(() => setLoading(false), 1000);
  };

  const handleSave = () => {
    sendCommand("session:save");
    setMsg("Session saved");
    setTimeout(() => { setMsg(""); handleRefresh(); }, 2000);
  };

  const handleLoad = (sid: string) => {
    sendCommand(`session:load:${sid}`);
    setMsg(`Loaded: ${sid}`);
    setTimeout(() => setMsg(""), 3000);
  };

  return (
    <div style={styles.container}>
      <div style={styles.header}>
        <span style={{ fontWeight: 600, fontSize: 14 }}>Sessions</span>
        <div style={{ display: "flex", gap: 8 }}>
          <button style={{ ...styles.btn, color: "#44cc88", borderColor: "#2a4a3a" }} onClick={handleSave}>
            Save Current
          </button>
          <button style={styles.btn} onClick={handleRefresh} disabled={loading}>
            {loading ? "..." : "Refresh"}
          </button>
        </div>
      </div>
      <div style={styles.content}>
        {msg && <div style={styles.msg}>{msg}</div>}
        {messages.length > 0 && (
          <div style={styles.info}>
            <span style={{ color: "#888", fontSize: 12 }}>
              {messages.length} messages in current session
            </span>
          </div>
        )}
        <div style={styles.sectionTitle}>Saved Sessions</div>
        {sessions.length === 0 ? (
          <div style={styles.empty}>
            No saved sessions. Chat with the agent then click "Save Current".
          </div>
        ) : (
          <div style={styles.list}>
            {sessions.map((sid) => (
              <div key={sid} style={styles.item}>
                <div style={styles.itemName}>{sid}</div>
                <button style={styles.loadBtn} onClick={() => handleLoad(sid)}>
                  Load
                </button>
              </div>
            ))}
          </div>
        )}
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
  content: { flex: 1, overflowY: "auto", padding: 24 },
  sectionTitle: {
    fontSize: 12, fontWeight: 600, color: "#888",
    marginBottom: 10, textTransform: "uppercase", letterSpacing: 1,
  },
  btn: {
    padding: "6px 14px", borderRadius: 6, border: "1px solid #3a3a5c",
    background: "#252540", color: "#bbb", cursor: "pointer", fontSize: 12,
  },
  msg: {
    padding: "8px 14px", borderRadius: 8, background: "#1a2a1a",
    border: "1px solid #2a4a2a", color: "#44cc88", fontSize: 13,
    marginBottom: 16,
  },
  info: { marginBottom: 16 },
  empty: {
    padding: 24, textAlign: "center", color: "#666", fontSize: 13,
    background: "#1a1a30", borderRadius: 8, border: "1px solid #2a2a3e",
  },
  list: { display: "flex", flexDirection: "column", gap: 6 },
  item: {
    display: "flex", justifyContent: "space-between", alignItems: "center",
    padding: "10px 14px", borderRadius: 8,
    background: "#1a1a30", border: "1px solid #2a2a3e",
  },
  itemName: {
    fontSize: 12, fontFamily: "'JetBrains Mono', monospace", color: "#aaa",
    maxWidth: "70%", overflow: "hidden", textOverflow: "ellipsis",
  },
  loadBtn: {
    padding: "4px 10px", borderRadius: 6, border: "1px solid #3a3a5c",
    background: "#252540", color: "#7c7cff", cursor: "pointer", fontSize: 11,
  },
};
