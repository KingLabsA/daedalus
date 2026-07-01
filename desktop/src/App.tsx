import React, { useState, useEffect } from "react";
import ChatView from "./components/Chat/ChatView";
import KanbanBoard from "./components/Kanban/KanbanBoard";
import AgentDashboard from "./components/AgentView/AgentDashboard";
import Composer from "./components/Composer/Composer";
import SettingsPanel from "./components/Settings/SettingsPanel";
import LogViewer from "./components/LogViewer/LogViewer";
import GitPanel from "./components/Git/GitPanel";
import LspPanel from "./components/Lsp/LspPanel";
import SessionPanel from "./components/Sessions/SessionPanel";
import FileExplorer from "./components/Files/FileExplorer";
import { WsProvider, useWs } from "./hooks/useWebSocket";
import { useStore } from "./store/session";

type Tab = "chat" | "kanban" | "agents" | "composer" | "git" | "files" | "lsp" | "sessions" | "logs" | "settings";

function ConnectionBar() {
  const { connected, connecting } = useWs();
  return (
    <div style={{
      height: 2,
      background: connected ? "#44cc88" : connecting ? "#ffaa44" : "#ff5555",
      transition: "background 0.4s",
      flexShrink: 0,
    }} />
  );
}

function ThemeToggle() {
  const theme = useStore((s) => s.theme);
  const setTheme = useStore((s) => s.setTheme);
  return (
    <button
      onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
      style={{
        width: 60, height: 36, border: "none", borderRadius: 8, cursor: "pointer",
        background: theme === "dark" ? "#252540" : "#e8e8f0",
        color: theme === "dark" ? "#a0a0b8" : "#333",
        fontSize: 16, marginBottom: 8,
      }}
      title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
    >
      {theme === "dark" ? "☀" : "☾"}
    </button>
  );
}

function StatusBar() {
  const { connected } = useWs();
  const provider = useStore((s) => s.provider);
  const model = useStore((s) => s.model);
  const cost = useStore((s) => s.cost);
  const theme = useStore((s) => s.theme);
  const isDark = theme === "dark";

  const formatCost = (c: number | null) => {
    if (c === null || c === undefined) return "$0.00";
    return c < 0.01 ? "<$0.01" : `$${c.toFixed(2)}`;
  };
  const providerShort = (p: string) => {
    const map: Record<string, string> = {
      openai: "OpenAI", anthropic: "Anthropic", google: "Gemini",
      groq: "Groq", ollama: "Ollama", openrouter: "OpenRouter",
      xai: "xAI", deepseek: "DeepSeek", zhipu: "GLM",
      mistral: "Mistral", together: "Together", fireworks: "Fireworks",
      cohere: "Cohere", perplexity: "PPLX", novita: "Novita",
      bedrock: "Bedrock", azure: "Azure", huggingface: "HF",
      replicate: "Replicate", moonshot: "Moonshot",
    };
    return map[p] || p;
  };

  const totalCost = cost?.total_cost ?? 0;

  return (
    <div style={{
      height: 28, display: "flex", alignItems: "center", justifyContent: "space-between",
      padding: "0 16px", fontSize: 11, fontFamily: "'JetBrains Mono', monospace",
      borderTop: `1px solid ${isDark ? "#2a2a3e" : "#c0c0d0"}`,
      background: isDark ? "#12121f" : "#d8d8e0",
      color: isDark ? "#777" : "#666",
      flexShrink: 0,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
        <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
          <span style={{
            width: 6, height: 6, borderRadius: "50%",
            background: connected ? "#44cc88" : "#ff5555",
          }} />
          {connected ? "Connected" : "Disconnected"}
        </span>
        <span style={{ color: isDark ? "#999" : "#555" }}>|</span>
        <span>{providerShort(provider)} · {model}</span>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
        <span>Cost: <strong style={{ color: totalCost > 1 ? "#ff8866" : "#44cc88" }}>{formatCost(totalCost)}</strong></span>
        <span style={{ color: isDark ? "#555" : "#999" }}>⌘K focus · ⌘⇧H toggle window</span>
      </div>
    </div>
  );
}

function StartupOverlay() {
  const { connected, connecting } = useWs();
  const [dismissed, setDismissed] = useState(false);

  if (connected || dismissed) return null;

  return (
    <div style={startupStyles.overlay}>
      <div style={startupStyles.card}>
        {connecting ? (
          <>
            <div style={startupStyles.spinner} />
            <div style={startupStyles.title}>Starting Hermes Engine</div>
            <div style={startupStyles.sub}>
              Connecting to agent on ws://127.0.0.1:8765...
            </div>
          </>
        ) : (
          <>
            <div style={{ fontSize: 40, marginBottom: 12, color: "#ff5555" }}>✕</div>
            <div style={startupStyles.title}>Connection Failed</div>
            <div style={startupStyles.sub}>
              Ensure <code>python3 agent_ultimate.py ws</code> is running.
            </div>
            <button
              style={startupStyles.retryBtn}
              onClick={() => setDismissed(true)}
            >
              Dismiss
            </button>
          </>
        )}
      </div>
    </div>
  );
}

const startupStyles: Record<string, React.CSSProperties> = {
  overlay: {
    position: "fixed", inset: 0,
    background: "rgba(22, 22, 42, 0.92)",
    display: "flex", alignItems: "center", justifyContent: "center",
    zIndex: 1000, backdropFilter: "blur(4px)",
  },
  card: {
    background: "#1e1e34", border: "1px solid #2a2a3e", borderRadius: 16,
    padding: "40px 48px", display: "flex", flexDirection: "column",
    alignItems: "center", gap: 8, maxWidth: 400, textAlign: "center",
  },
  spinner: {
    width: 36, height: 36,
    border: "3px solid #2a2a3e", borderTop: "3px solid #7c7cff",
    borderRadius: "50%", animation: "spin 0.8s linear infinite",
    marginBottom: 8,
  },
  title: { fontSize: 18, fontWeight: 700, color: "#e0e0e0", letterSpacing: 0.5 },
  sub: { fontSize: 13, color: "#888", lineHeight: 1.5 },
  retryBtn: {
    marginTop: 12, padding: "8px 24px", borderRadius: 8,
    border: "1px solid #3a3a5c", background: "#252540",
    color: "#bbb", cursor: "pointer", fontSize: 13,
  },
};

function AppContent() {
  const [activeTab, setActiveTab] = useState<Tab>("chat");
  const { connected, subscribe } = useWs();
  const setLogs = useStore((s) => s.setLogs);
  const setDiff = useStore((s) => s.setDiff);
  const setLsp = useStore((s) => s.setLsp);
  const theme = useStore((s) => s.theme);

  useEffect(() => {
    const ul = subscribe("logs", (msg: unknown) => setLogs((msg as any).data ?? []));
    const ud = subscribe("diff", (msg: unknown) => setDiff((msg as any).data ?? ""));
    const us = subscribe("lsp", (msg: unknown) => setLsp((msg as any).data ?? ""));
    return () => { ul(); ud(); us(); };
  }, [subscribe, setLogs, setDiff, setLsp]);

  const tabs: { id: Tab; label: string; short: string }[] = [
    { id: "chat", label: "Chat", short: "CH" },
    { id: "kanban", label: "Kanban", short: "KB" },
    { id: "agents", label: "Dashboard", short: "DB" },
    { id: "composer", label: "Composer", short: "CP" },
    { id: "git", label: "Git", short: "GT" },
    { id: "files", label: "Files", short: "FL" },
    { id: "lsp", label: "LSP", short: "LS" },
    { id: "sessions", label: "Sessions", short: "SN" },
    { id: "logs", label: "Logs", short: "LG" },
    { id: "settings", label: "Settings", short: "ST" },
  ];

  const cssVars = theme === "dark"
    ? { bg: "#1a1a2e", text: "#e0e0e0", sidebarBg: "#16162a", border: "#2a2a3e" }
    : { bg: "#f0f0f5", text: "#1a1a2e", sidebarBg: "#e0e0e8", border: "#c0c0d0" };

  return (
    <div style={{ ...styles.container, background: cssVars.bg, color: cssVars.text }}>
      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
        @keyframes blink { 0%,100% { opacity: 1; } 50% { opacity: 0; } }
        * { scrollbar-width: thin; scrollbar-color: #3a3a5c transparent; }
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: #3a3a5c; border-radius: 3px; }
      `}</style>
      <StartupOverlay />
      <ConnectionBar />
      <div style={styles.body}>
        <nav style={{ ...styles.sidebar, background: cssVars.sidebarBg, borderRight: `1px solid ${cssVars.border}` }}>
          <div style={styles.logo}>HU</div>
          <ThemeToggle />
          {tabs.map((t) => (
            <button
              key={t.id}
              style={{
                ...styles.navBtn,
                background: activeTab === t.id ? "#2d2d3d" : "transparent",
              }}
              onClick={() => setActiveTab(t.id)}
              title={t.label}
            >
              <span style={styles.navIcon}>{t.short}</span>
              <span style={styles.navLabel}>{t.label}</span>
            </button>
          ))}
          <div style={{
            marginTop: "auto", width: 7, height: 7, borderRadius: "50%",
            background: connected ? "#44cc88" : "#ff5555",
            marginBottom: 16, transition: "background 0.3s",
          }} />
        </nav>
        <main style={styles.main}>
          {activeTab === "chat" && <ChatView />}
          {activeTab === "kanban" && <KanbanBoard />}
          {activeTab === "agents" && <AgentDashboard />}
          {activeTab === "composer" && <Composer />}
          {activeTab === "git" && <GitPanel />}
          {activeTab === "files" && <FileExplorer />}
          {activeTab === "lsp" && <LspPanel />}
          {activeTab === "sessions" && <SessionPanel />}
          {activeTab === "logs" && <LogViewer />}
          {activeTab === "settings" && <SettingsPanel />}
        </main>
      </div>
      <StatusBar />
    </div>
  );
}

export default function App() {
  return (
    <WsProvider>
      <AppContent />
    </WsProvider>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: "flex", flexDirection: "column", height: "100vh",
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  body: { display: "flex", flex: 1, overflow: "hidden" },
  sidebar: {
    width: 72, display: "flex", flexDirection: "column",
    alignItems: "center", padding: "12px 0", gap: 4,
  },
  logo: {
    fontSize: 16, fontWeight: 700, color: "#7c7cff", marginBottom: 8,
    letterSpacing: 2, fontFamily: "'JetBrains Mono', monospace",
  },
  navBtn: {
    width: 60, height: 48, border: "none", borderRadius: 10, cursor: "pointer",
    display: "flex", flexDirection: "column", alignItems: "center",
    justifyContent: "center", gap: 2, color: "#a0a0b8", transition: "all 0.15s",
  },
  navIcon: {
    fontSize: 12, fontWeight: 700,
    fontFamily: "'JetBrains Mono', monospace", letterSpacing: 1,
  },
  navLabel: { fontSize: 10, fontWeight: 500 },
  main: { flex: 1, display: "flex", overflow: "hidden" },
};
