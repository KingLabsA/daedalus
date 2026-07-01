import React from "react";
import { useStore } from "../../store/session";

const STATUS_COLORS: Record<string, string> = {
  idle: "#44cc88",
  working: "#ffaa44",
  error: "#ff5555",
};

const SAFETY_COLORS: Record<string, string> = {
  suggest: "#ffaa44",
  plan: "#66aaff",
  auto: "#44cc88",
};

export default function AgentDashboard() {
  const {
    connected, connecting, provider, model,
    tools, skills, messages, kanban,
    safetyMode, checkpoints, indexStats, pendingApprovals,
    cost,
  } = useStore();

  const totalTasks = kanban.todo.length + kanban.in_progress.length +
    kanban.review.length + kanban.done.length;
  const activeTasks = kanban.in_progress.length + kanban.review.length;

  return (
    <div style={styles.container}>
      <div style={styles.header}>
        <span style={{ fontWeight: 600, fontSize: 14 }}>Agent Dashboard</span>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <span style={styles.safetyBadge}>
            <span style={{ display: "inline-block", width: 6, height: 6, borderRadius: "50%", background: SAFETY_COLORS[safetyMode], marginRight: 4 }} />
            {safetyMode}
          </span>
          <span style={{
            ...styles.statusBadge,
            background: connected ? "#1a3a2a" : connecting ? "#3a3a1a" : "#3a1a1a",
            color: connected ? "#44cc88" : connecting ? "#ffaa44" : "#ff5555",
          }}>
            {connected ? "Connected" : connecting ? "Connecting..." : "Offline"}
          </span>
        </div>
      </div>

      <div style={styles.grid}>
        <div style={styles.card}>
          <div style={styles.cardTitle}>Provider</div>
          <div style={styles.cardValue}>{provider}</div>
          <div style={styles.cardSub}>{model}</div>
        </div>
        <div style={styles.card}>
          <div style={styles.cardTitle}>Tools</div>
          <div style={styles.cardValue}>{tools.length}</div>
          <div style={styles.cardSub}>registered</div>
        </div>
        <div style={styles.card}>
          <div style={styles.cardTitle}>Skills</div>
          <div style={styles.cardValue}>{skills.length}</div>
          <div style={styles.cardSub}>learned</div>
        </div>
        <div style={styles.card}>
          <div style={styles.cardTitle}>Tasks</div>
          <div style={styles.cardValue}>{totalTasks}</div>
          <div style={styles.cardSub}>{activeTasks} active</div>
        </div>
        <div style={styles.card}>
          <div style={styles.cardTitle}>Messages</div>
          <div style={styles.cardValue}>{messages.length}</div>
          <div style={styles.cardSub}>this session</div>
        </div>
        <div style={styles.card}>
          <div style={styles.cardTitle}>Cost</div>
          <div style={styles.cardValue}>{cost ? `$${cost.total_cost.toFixed(4)}` : "—"}</div>
          <div style={styles.cardSub}>{cost ? `${cost.session_calls} calls` : "no data"}</div>
        </div>
      </div>

      <div style={styles.row}>
        <div style={styles.halfSection}>
          <div style={styles.sectionTitle}>Kanban</div>
          <div style={styles.kanbanGrid}>
            <div style={styles.kanbanCol}>
              <div style={styles.kanbanHeader}>Todo ({kanban.todo.length})</div>
              {kanban.todo.slice(0, 3).map((t, i) => (
                <div key={i} style={styles.kanbanItem}>{typeof t === 'string' ? t : (t as any).title || JSON.stringify(t)}</div>
              ))}
            </div>
            <div style={styles.kanbanCol}>
              <div style={styles.kanbanHeader}>In Progress ({kanban.in_progress.length})</div>
              {kanban.in_progress.slice(0, 3).map((t, i) => (
                <div key={i} style={{ ...styles.kanbanItem, borderLeftColor: "#ffaa44" }}>{typeof t === 'string' ? t : (t as any).title || JSON.stringify(t)}</div>
              ))}
            </div>
            <div style={styles.kanbanCol}>
              <div style={styles.kanbanHeader}>Review ({kanban.review.length})</div>
              {kanban.review.slice(0, 3).map((t, i) => (
                <div key={i} style={{ ...styles.kanbanItem, borderLeftColor: "#66aaff" }}>{typeof t === 'string' ? t : (t as any).title || JSON.stringify(t)}</div>
              ))}
            </div>
            <div style={styles.kanbanCol}>
              <div style={styles.kanbanHeader}>Done ({kanban.done.length})</div>
              {kanban.done.slice(0, 3).map((t, i) => (
                <div key={i} style={{ ...styles.kanbanItem, borderLeftColor: "#44cc88" }}>{typeof t === 'string' ? t : (t as any).title || JSON.stringify(t)}</div>
              ))}
            </div>
          </div>
        </div>

        <div style={styles.halfSection}>
          <div style={styles.sectionTitle}>Checkpoints</div>
          {checkpoints.length === 0 && (
            <div style={{ color: "#555", fontSize: 13, padding: "8px 0" }}>No checkpoints yet.</div>
          )}
          {checkpoints.slice(0, 5).map((cp) => (
            <div key={cp.id} style={styles.miniCard}>
              <div style={{ fontSize: 12, color: "#ccc" }}>{cp.label}</div>
              <div style={{ fontSize: 10, color: "#888" }}>{cp.filesChanged} files | {new Date(cp.timestamp * 1000).toLocaleTimeString()}</div>
            </div>
          ))}
          {indexStats && (
            <div style={{ marginTop: 10 }}>
              <div style={styles.sectionTitle}>Index</div>
              <div style={styles.miniCard}>
                <div style={{ fontSize: 12, color: "#ccc" }}>{indexStats.totalFiles} files / {indexStats.totalChunks} chunks</div>
              </div>
            </div>
          )}
        </div>
      </div>

      {pendingApprovals.length > 0 && (
        <div style={styles.section}>
          <div style={styles.sectionTitle}>Pending Approvals ({pendingApprovals.length})</div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {pendingApprovals.map((a) => (
              <div key={a.id} style={styles.approvalBadge}>
                <span style={{ color: "#ffaa44", fontWeight: 600 }}>{a.tool}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {skills.length > 0 && (
        <div style={styles.section}>
          <div style={styles.sectionTitle}>Learned Skills</div>
          <div style={styles.skillList}>
            {skills.map((s) => (
              <div key={s} style={styles.skillChip}>{s}</div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: { display: "flex", flexDirection: "column", height: "100%", flex: 1, overflowY: "auto" },
  header: {
    padding: "10px 20px",
    borderBottom: "1px solid #2a2a3e",
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    minHeight: 48,
  },
  statusBadge: {
    fontSize: 11,
    padding: "3px 10px",
    borderRadius: 20,
    fontWeight: 500,
  },
  safetyBadge: {
    fontSize: 10,
    padding: "3px 8px",
    borderRadius: 10,
    background: "#1a1a30",
    border: "1px solid #3a3a5c",
    color: "#aaa",
    display: "flex",
    alignItems: "center",
    textTransform: "uppercase",
    letterSpacing: 0.5,
  },
  grid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))",
    gap: 10,
    padding: 16,
  },
  card: {
    background: "#1e1e34",
    borderRadius: 10,
    padding: 14,
    border: "1px solid #2a2a3e",
  },
  cardTitle: { fontSize: 10, color: "#777", textTransform: "uppercase", letterSpacing: 1, marginBottom: 4 },
  cardValue: { fontSize: 20, fontWeight: 700, color: "#e0e0e0" },
  cardSub: { fontSize: 11, color: "#555", marginTop: 2 },
  row: {
    display: "flex",
    gap: 16,
    padding: "0 16px 16px",
  },
  halfSection: {
    flex: 1,
    minWidth: 0,
  },
  section: { padding: "0 16px 16px" },
  sectionTitle: {
    fontSize: 11, fontWeight: 600, color: "#888",
    textTransform: "uppercase", letterSpacing: 1, marginBottom: 10,
  },
  kanbanGrid: {
    display: "flex",
    gap: 8,
  },
  kanbanCol: {
    flex: 1,
    minWidth: 0,
  },
  kanbanHeader: {
    fontSize: 10,
    fontWeight: 600,
    color: "#888",
    textTransform: "uppercase",
    letterSpacing: 0.5,
    marginBottom: 6,
  },
  kanbanItem: {
    padding: "4px 8px",
    borderRadius: 4,
    background: "#1a1a30",
    border: "1px solid #2a2a3e",
    borderLeft: "2px solid #888",
    fontSize: 11,
    color: "#aaa",
    marginBottom: 4,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap" as const,
  },
  miniCard: {
    padding: "6px 10px",
    borderRadius: 6,
    background: "#1a1a30",
    border: "1px solid #2a2a3e",
    marginBottom: 6,
  },
  approvalBadge: {
    padding: "4px 10px",
    borderRadius: 6,
    background: "#2a2a1a",
    border: "1px solid #5a5a3a",
    fontSize: 11,
  },
  skillList: { display: "flex", flexWrap: "wrap", gap: 6 },
  skillChip: {
    padding: "3px 10px", borderRadius: 6, fontSize: 11,
    background: "#252540", border: "1px solid #3a3a5c", color: "#aaa",
  },
};
