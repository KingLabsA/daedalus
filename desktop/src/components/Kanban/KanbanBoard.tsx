import React, { useState } from "react";
import { useKanban } from "../../hooks/useKanban";

const COLORS: Record<string, string> = {
  todo: "#4a4a6a",
  in_progress: "#7c7cff",
  review: "#ffaa44",
  done: "#44cc88",
};

const HEADER_LABELS: Record<string, string> = {
  todo: "To Do",
  in_progress: "In Progress",
  review: "Review",
  done: "Done",
};

export default function KanbanBoard() {
  const { kanban, addTask, moveTask, removeTask } = useKanban();
  const [newTask, setNewTask] = useState("");

  const handleAdd = () => {
    if (!newTask.trim()) return;
    addTask(newTask);
    setNewTask("");
  };

  const columns = ["todo", "in_progress", "review", "done"] as const;

  return (
    <div style={styles.container}>
      <div style={styles.header}>
        <span style={{ fontWeight: 600, fontSize: 14 }}>Kanban Board</span>
        <div style={styles.addRow}>
          <input
            style={styles.input}
            value={newTask}
            onChange={(e) => setNewTask(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleAdd()}
            placeholder="New task..."
          />
          <button style={styles.addBtn} onClick={handleAdd}>+</button>
        </div>
      </div>
      <div style={styles.board}>
        {columns.map((key) => (
          <div key={key} style={styles.column}>
            <div style={styles.colHeader}>
              <span>{HEADER_LABELS[key]}</span>
              <span style={styles.colCount}>{kanban[key].length}</span>
            </div>
            <div style={styles.cardList}>
              {kanban[key].map((task) => (
                <div key={task.id} style={styles.card}>
                  <div style={styles.cardHeader}>
                    <span style={{ fontWeight: 500 }}>{task.title}</span>
                    <button
                      style={styles.rmBtn}
                      onClick={() => removeTask(task.id)}
                      title="Remove"
                    >&times;</button>
                  </div>
                  <div style={styles.cardMeta}>
                    {task.retries > 0 && `${task.retries} retries`}
                    {task.assigned_to && ` | ${task.assigned_to}`}
                  </div>
                  <div style={styles.cardActions}>
                    {key !== "todo" && (
                      <button
                        style={styles.moveBtn}
                        onClick={() => {
                          const idx = columns.indexOf(key);
                          if (idx > 0) moveTask(task.id, columns[idx - 1]);
                        }}
                      >
                        &larr;
                      </button>
                    )}
                    {key !== "done" && (
                      <button
                        style={styles.moveBtn}
                        onClick={() => {
                          const idx = columns.indexOf(key);
                          if (idx < columns.length - 1) moveTask(task.id, columns[idx + 1]);
                        }}
                      >
                        &rarr;
                      </button>
                    )}
                  </div>
                </div>
              ))}
              {kanban[key].length === 0 && (
                <div style={styles.emptyCol}>No tasks</div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: { display: "flex", flexDirection: "column", height: "100%", flex: 1 },
  header: {
    padding: "10px 20px",
    borderBottom: "1px solid #2a2a3e",
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    minHeight: 48,
  },
  addRow: { display: "flex", gap: 8 },
  input: {
    padding: "7px 12px",
    borderRadius: 8,
    border: "1px solid #3a3a5c",
    background: "#1e1e36",
    color: "#e0e0e0",
    fontSize: 13,
    width: 200,
    outline: "none",
  },
  addBtn: {
    padding: "7px 14px",
    borderRadius: 8,
    border: "none",
    background: "#7c7cff",
    color: "#fff",
    cursor: "pointer",
    fontWeight: 700,
    fontSize: 15,
  },
  board: {
    flex: 1,
    display: "flex",
    gap: 12,
    padding: 16,
    overflowX: "auto",
  },
  column: {
    flex: 1,
    minWidth: 220,
    background: "#1e1e34",
    borderRadius: 12,
    padding: 12,
  },
  colHeader: {
    fontSize: 13,
    fontWeight: 600,
    marginBottom: 12,
    paddingBottom: 8,
    borderBottom: "2px solid #2a2a4e",
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    textTransform: "uppercase",
    letterSpacing: 0.5,
    color: "#aaa",
  },
  colCount: {
    fontSize: 11,
    color: "#666",
    background: "#2a2a3e",
    padding: "1px 7px",
    borderRadius: 10,
  },
  cardList: { display: "flex", flexDirection: "column", gap: 8 },
  emptyCol: { fontSize: 12, color: "#555", textAlign: "center", padding: "16px 0" },
  card: {
    background: "#252540",
    borderRadius: 10,
    padding: "10px 12px",
    fontSize: 13,
    border: "1px solid #333",
  },
  cardHeader: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
  },
  rmBtn: {
    border: "none",
    background: "transparent",
    color: "#555",
    cursor: "pointer",
    fontSize: 16,
    lineHeight: 1,
    padding: 0,
  },
  cardMeta: { fontSize: 11, color: "#666", marginTop: 3 },
  cardActions: { display: "flex", gap: 4, marginTop: 8 },
  moveBtn: {
    padding: "3px 10px",
    borderRadius: 6,
    border: "1px solid #444",
    background: "transparent",
    color: "#aaa",
    cursor: "pointer",
    fontSize: 13,
  },
};
