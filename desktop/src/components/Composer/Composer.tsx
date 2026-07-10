import React, { useState } from "react";
import { useStore } from "../../store/session";
import { useAgent } from "../../hooks/useAgent";
import { useWs } from "../../hooks/useWebSocket";

// model list comes live from the agent (WS `models` command) — no hardcoding

const SAFETY_COLORS: Record<string, string> = {
  suggest: "#ffaa44",
  plan: "#66aaff",
  auto: "#44cc88",
};

export default function Composer() {
  const [prompt, setPrompt] = useState("");
  const [mode, setMode] = useState<"chat" | "goal" | "multitask">("chat");
  const { send, loading } = useAgent();
  const { switchModel } = useWs();
  const skills = useStore((s) => s.skills);
  const tools = useStore((s) => s.tools);
  const provider = useStore((s) => s.provider);
  const model = useStore((s) => s.model);
  const safetyMode = useStore((s) => s.safetyMode);
  const setModel = useStore((s) => s.setModel);
  const modelList = useStore((s) => s.modelList);

  const handleRun = () => {
    if (!prompt.trim() || loading) return;
    if (mode === "goal") send(`/goal ${prompt}`);
    else if (mode === "multitask") send(`/multitask ${prompt}`);
    else send(prompt);
    setPrompt("");
  };

  const handleModelSwitch = (m: string) => {
    setModel(m);
    switchModel(m);
  };

  const MODE_DESC: Record<string, string> = {
    chat: "Send a prompt to the agent",
    goal: "Define a goal — agent works until COMPLETE",
    multitask: "Comma-separated sub-tasks for parallel execution",
  };

  const MODE_PLACEHOLDER: Record<string, string> = {
    chat: "Write a Python script that...",
    goal: "Create a full REST API with tests...",
    multitask: "Build login page, Create database schema, Write API tests",
  };

  return (
    <div style={styles.container}>
      <div style={styles.header}>
        <span style={{ fontWeight: 600, fontSize: 14 }}>Composer</span>
        <div style={styles.headerRight}>
          <div style={styles.safetyBadge}>
            <span style={{ display: "inline-block", width: 6, height: 6, borderRadius: "50%", background: SAFETY_COLORS[safetyMode], marginRight: 5 }} />
            {safetyMode}
          </div>
          <select
            style={styles.modelSelect}
            value={model}
            onChange={(e) => handleModelSwitch(e.target.value)}
          >
            {(modelList?.models?.length ? modelList.models : [model]).map((m) => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>
          <div style={styles.modeRow}>
            {(["chat", "goal", "multitask"] as const).map((m) => (
              <button
                key={m}
                style={{
                  ...styles.modeBtn,
                  background: mode === m ? "#7c7cff" : "transparent",
                  color: mode === m ? "#fff" : "#888",
                  borderColor: mode === m ? "#7c7cff" : "#3a3a5c",
                }}
                onClick={() => setMode(m)}
              >
                {m}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div style={{ flex: 1, padding: 20, overflowY: "auto" }}>
        <div style={styles.sectionTitle}>{MODE_DESC[mode]}</div>

        <textarea
          style={styles.textarea}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder={MODE_PLACEHOLDER[mode]}
          rows={8}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              handleRun();
            }
          }}
        />

        <button
          style={{
            ...styles.runBtn,
            opacity: loading || !prompt.trim() ? 0.5 : 1,
          }}
          onClick={handleRun}
          disabled={loading || !prompt.trim()}
        >
          {loading ? "Running..." : "Run"} <span style={{ fontSize: 10, opacity: 0.7 }}>(⌘Enter)</span>
        </button>

        <div style={{ ...styles.sectionTitle, marginTop: 28 }}>Available Tools</div>
        <div style={styles.chipGrid}>
          {tools.length === 0 && (
            <div style={{ color: "#555", fontSize: 13 }}>Tools appear when agent is connected.</div>
          )}
          {tools.map((t) => (
            <div key={t} style={styles.toolChip}
              onClick={() => setPrompt((p) => p + ` /tool:${t} `)}>
              {t}
            </div>
          ))}
        </div>

        {skills.length > 0 && (
          <>
            <div style={{ ...styles.sectionTitle, marginTop: 20 }}>Learned Skills</div>
            <div style={styles.chipGrid}>
              {skills.map((s) => (
                <div key={s} style={{ ...styles.toolChip, borderColor: "#3a5a3a", color: "#7a7" }}>
                  {s}
                </div>
              ))}
            </div>
          </>
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
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    minHeight: 48,
  },
  headerRight: {
    display: "flex",
    alignItems: "center",
    gap: 10,
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
  modelSelect: {
    padding: "4px 8px",
    borderRadius: 6,
    border: "1px solid #3a3a5c",
    background: "#1e1e36",
    color: "#e0e0e0",
    fontSize: 11,
    outline: "none",
  },
  modeRow: { display: "flex", gap: 4 },
  modeBtn: {
    padding: "5px 14px",
    borderRadius: 6,
    border: "1px solid #3a3a5c",
    cursor: "pointer",
    fontSize: 11,
    fontWeight: 500,
    textTransform: "uppercase",
    letterSpacing: 0.5,
  },
  sectionTitle: {
    fontSize: 12, fontWeight: 600, color: "#888",
    textTransform: "uppercase", letterSpacing: 1, marginBottom: 10,
  },
  textarea: {
    width: "100%",
    padding: 14,
    borderRadius: 12,
    border: "1px solid #3a3a5c",
    background: "#1e1e36",
    color: "#e0e0e0",
    fontSize: 14,
    resize: "vertical",
    fontFamily: "'Inter', system-ui, sans-serif",
    boxSizing: "border-box",
    outline: "none",
    lineHeight: 1.5,
  },
  runBtn: {
    marginTop: 12,
    padding: "9px 24px",
    borderRadius: 8,
    border: "none",
    background: "#7c7cff",
    color: "#fff",
    fontSize: 13,
    fontWeight: 600,
    cursor: "pointer",
  },
  chipGrid: { display: "flex", flexWrap: "wrap", gap: 6 },
  toolChip: {
    padding: "3px 10px",
    borderRadius: 6,
    fontSize: 11,
    cursor: "pointer",
    background: "#252540",
    border: "1px solid #3a3a5c",
    color: "#aaa",
    fontFamily: "'JetBrains Mono', monospace",
  },
};
