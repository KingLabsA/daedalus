import React, { useEffect, useState } from "react";
import { useStore } from "../../store/session";
import { AgentWorker } from "../../types";

function generateMockWorkers(connected: boolean): AgentWorker[] {
  if (!connected) return [];
  return [
    { id: "w1", name: "Main", type: "llm", status: "idle", lastHeartbeat: new Date() },
    { id: "w2", name: "Browser", type: "browser", status: "idle", lastHeartbeat: new Date() },
    { id: "w3", name: "Desktop", type: "desktop", status: "idle", lastHeartbeat: new Date() },
    { id: "w4", name: "Sandbox", type: "docker", status: "idle", lastHeartbeat: new Date() },
  ];
}

const STATUS_COLORS: Record<string, string> = {
  idle: "#44cc88",
  working: "#ffaa44",
  error: "#ff5555",
};

export default function AgentDashboard() {
  const {
    connected, connecting, provider, model,
    tools, skills, messages, kanban,
  } = useStore();
  const [workers] = useState<AgentWorker[]>(() =>
    generateMockWorkers(connected)
  );

  const totalTasks = kanban.todo.length + kanban.in_progress.length +
    kanban.review.length + kanban.done.length;
  const activeTasks = kanban.in_progress.length + kanban.review.length;

  return (
    <div style={styles.container}>
      <div style={styles.header}>
        <span style={{ fontWeight: 600, fontSize: 14 }}>Agent Dashboard</span>
        <span style={{
          ...styles.statusBadge,
          background: connected ? "#1a3a2a" : connecting ? "#3a3a1a" : "#3a1a1a",
          color: connected ? "#44cc88" : connecting ? "#ffaa44" : "#ff5555",
        }}>
          {connected ? "Connected" : connecting ? "Connecting..." : "Offline"}
        </span>
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
          <div style={styles.cardTitle}>Kanban</div>
          <div style={styles.cardKanban}>
            <span>{kanban.todo.length}<span style={styles.kanbanLabel}>todo</span></span>
            <span>{kanban.in_progress.length}<span style={styles.kanbanLabel}>wip</span></span>
            <span>{kanban.review.length}<span style={styles.kanbanLabel}>review</span></span>
            <span>{kanban.done.length}<span style={styles.kanbanLabel}>done</span></span>
          </div>
        </div>
      </div>

      <div style={styles.section}>
        <div style={styles.sectionTitle}>Workers</div>
        <div style={styles.workerGrid}>
          {workers.length === 0 && (
            <div style={{ color: "#555", fontSize: 13, padding: "8px 0" }}>
              No active workers. Connect to the agent.
            </div>
          )}
          {workers.map((w) => (
            <div key={w.id} style={styles.workerCard}>
              <div style={styles.workerHeader}>
                <span style={{ fontWeight: 600, fontSize: 13 }}>{w.name}</span>
                <span style={styles.workerStatus}>
                  <span style={{
                    display: "inline-block", width: 6, height: 6,
                    borderRadius: "50%", background: STATUS_COLORS[w.status] || "#888",
                    marginRight: 5,
                  }} />
                  {w.status}
                </span>
              </div>
              <div style={{ fontSize: 11, color: "#666", marginTop: 4 }}>
                {w.type}
              </div>
            </div>
          ))}
        </div>
      </div>

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
  grid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fill, minmax(170px, 1fr))",
    gap: 12,
    padding: 16,
  },
  card: {
    background: "#1e1e34",
    borderRadius: 12,
    padding: 16,
    border: "1px solid #2a2a3e",
  },
  cardTitle: { fontSize: 10, color: "#777", textTransform: "uppercase", letterSpacing: 1, marginBottom: 6 },
  cardValue: { fontSize: 22, fontWeight: 700, color: "#e0e0e0" },
  cardSub: { fontSize: 11, color: "#555", marginTop: 3 },
  cardKanban: {
    display: "flex",
    gap: 12,
    fontSize: 15,
    fontWeight: 700,
    color: "#ccc",
    marginTop: 4,
  },
  kanbanLabel: {
    fontSize: 9,
    color: "#666",
    display: "block",
    fontWeight: 500,
    textTransform: "uppercase",
    letterSpacing: 0.5,
  },
  section: { padding: "0 16px 16px" },
  sectionTitle: {
    fontSize: 11, fontWeight: 600, color: "#888",
    textTransform: "uppercase", letterSpacing: 1, marginBottom: 10,
  },
  workerGrid: { display: "flex", gap: 10, flexWrap: "wrap" },
  workerCard: {
    background: "#1e1e34", borderRadius: 10, padding: 12,
    border: "1px solid #2a2a3e", minWidth: 150,
  },
  workerHeader: { display: "flex", justifyContent: "space-between", alignItems: "center" },
  workerStatus: { fontSize: 11, color: "#aaa", display: "flex", alignItems: "center" },
  skillList: { display: "flex", flexWrap: "wrap", gap: 6 },
  skillChip: {
    padding: "3px 10px", borderRadius: 6, fontSize: 11,
    background: "#252540", border: "1px solid #3a3a5c", color: "#aaa",
  },
};
