import React, { useState, useEffect } from "react";
import { useStore } from "../../store/session";
import { useWs } from "../../hooks/useWebSocket";

const PROVIDERS = [
  { id: "openai", label: "OpenAI" },
  { id: "anthropic", label: "Anthropic Claude" },
  { id: "openrouter", label: "OpenRouter (100+ models)" },
  { id: "ollama", label: "Ollama (local)" },
  { id: "google", label: "Google Gemini" },
  { id: "deepseek", label: "DeepSeek" },
  { id: "zhipu", label: "Zhipu (GLM)" },
  { id: "moonshot", label: "Moonshot (Kimi)" },
  { id: "xai", label: "xAI (Grok)" },
  { id: "mistral", label: "Mistral AI" },
  { id: "cohere", label: "Cohere" },
  { id: "together", label: "Together AI" },
  { id: "fireworks", label: "Fireworks AI" },
  { id: "groq", label: "Groq" },
  { id: "perplexity", label: "Perplexity" },
  { id: "novita", label: "Novita" },
];

const FEATURES = [
  "Self-Learning (Skills)",
  "Self-Healing Retries",
  "Self-Verification (pytest)",
  "Self-Implementation",
  "Self-Correction Loop",
  "Browser Automation",
  "Desktop Control",
  "Multi-Agent Kanban",
  "Dynamic Workflows",
  "Context Compression",
  "Persistent Memory (SQLite)",
  "Parallel Execution",
  "Plugin Marketplace",
  "Docker Sandbox",
  "Codebase Indexing",
  "Git Checkpoints",
  "Safety Modes",
  "Lifecycle Hooks",
  "Model Switching",
  "Cost Tracking",
];

const PROVIDER_MODELS: Record<string, string[]> = {
  openai: ["gpt-4o-mini", "gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo", "o1-mini", "o1-preview"],
  anthropic: ["claude-sonnet-4-20250514", "claude-sonnet-4", "claude-3-5-sonnet-latest", "claude-3-5-haiku-latest", "claude-3-opus-latest"],
  openrouter: ["auto"],
  ollama: ["llama3.2", "llama3.1", "qwen2.5", "mistral", "mixtral", "codellama", "deepseek-coder"],
  google: ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash"],
  deepseek: ["deepseek-chat", "deepseek-coder"],
  zhipu: ["glm-4-plus", "glm-4", "glm-4v"],
  moonshot: ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
  xai: ["grok-2", "grok-2-mini"],
  mistral: ["mistral-large-latest", "mistral-medium-latest", "mistral-small-latest", "codestral-latest"],
  cohere: ["command-r-plus", "command-r", "command"],
  together: ["meta-llama/Llama-3.3-70B-Instruct-Turbo", "meta-llama/Llama-3.2-3B-Instruct-Turbo", "mistralai/Mixtral-8x22B-Instruct-v0.1"],
  fireworks: ["accounts/fireworks/models/llama-v3p3-70b-instruct", "accounts/fireworks/models/llama-v3p2-3b-instruct", "accounts/fireworks/models/mixtral-8x22b-instruct"],
  groq: ["llama-3.3-70b-versatile", "llama-3.2-90b-vision-preview", "mixtral-8x7b-32768", "gemma2-9b-it", "deepseek-r1-distill-llama-70b"],
  perplexity: ["sonar-pro", "sonar", "sonar-deep-research"],
  novita: ["meta-llama/llama-3.1-8b-instruct", "meta-llama/llama-3.1-70b-instruct", "deepseek/deepseek-r1", "mistralai/mistral-7b-instruct"],
};

const SAFETY_MODES = [
  { id: "suggest", label: "Suggest", desc: "Agent suggests changes, you approve each one" },
  { id: "plan", label: "Plan", desc: "Agent creates a plan, you approve before execution" },
  { id: "auto", label: "Auto", desc: "Agent runs autonomously with safety rails" },
] as const;

export default function SettingsPanel() {
  const [systemPrompt, setSystemPrompt] = useState("");
  const [indexQuery, setIndexQuery] = useState("");
  const [indexResults, setIndexResults] = useState<string[]>([]);
  const provider = useStore((s) => s.provider);
  const model = useStore((s) => s.model);
  const connected = useStore((s) => s.connected);
  const cost = useStore((s) => s.cost);
  const providerTestResult = useStore((s) => s.providerTestResult);
  const safetyMode = useStore((s) => s.safetyMode);
  const checkpoints = useStore((s) => s.checkpoints);
  const indexStats = useStore((s) => s.indexStats);
  const hookLog = useStore((s) => s.hookLog);
  const setProvider = useStore((s) => s.setProvider);
  const setModel = useStore((s) => s.setModel);
  const { switchProvider, switchModel, switchSafety, sendCommand, subscribe } = useWs();
  const [testing, setTesting] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"general" | "safety" | "checkpoints" | "index" | "hooks">("general");

  const handleProviderChange = (p: string) => {
    setProvider(p);
    switchProvider(p);
  };

  const handleModelChange = (m: string) => {
    setModel(m);
    switchModel(m);
  };

  const handleTestConnection = async (p: string) => {
    setTesting(p);
    sendCommand(`provider:test:${p}`);
    setTimeout(() => setTesting(null), 5000);
  };

  const handleFetchCost = () => sendCommand("cost");

  const handleSafetyChange = (mode: "suggest" | "plan" | "auto") => {
    switchSafety(mode);
  };

  const handleCreateCheckpoint = () => {
    const label = prompt("Checkpoint label (optional):") || `checkpoint-${Date.now()}`;
    sendCommand(`checkpoint:create:${label}`);
  };

  const handleRollback = (id: string) => {
    if (confirm("Rollback to this checkpoint? This will undo changes.")) {
      sendCommand(`checkpoint:restore:${id}`);
    }
  };

  const handleIndexSearch = () => {
    if (!indexQuery.trim()) return;
    sendCommand(`index:search:${indexQuery}`);
  };

  const handleReindex = () => {
    sendCommand("index:reindex");
  };

  useEffect(() => {
    const unsub1 = subscribe("system_prompt", (data: unknown) => {
      setSystemPrompt((data as any).data || "");
    });
    const unsub2 = subscribe("index_results", (data: unknown) => {
      setIndexResults((data as any).data || []);
    });
    sendCommand("system_prompt");
    return () => { unsub1(); unsub2(); };
  }, [subscribe, sendCommand]);

  const handleSavePrompt = () => {
    sendCommand(`system_prompt:set:${systemPrompt}`);
  };

  const tabs = [
    { id: "general" as const, label: "General" },
    { id: "safety" as const, label: "Safety" },
    { id: "checkpoints" as const, label: "Checkpoints" },
    { id: "index" as const, label: "Index" },
    { id: "hooks" as const, label: "Hooks" },
  ];

  return (
    <div style={styles.container}>
      <div style={styles.header}>
        <span style={{ fontWeight: 600, fontSize: 14 }}>Settings</span>
        <span style={{
          fontSize: 11, padding: "2px 10px", borderRadius: 10,
          background: connected ? "#1a3a2a" : "#3a1a1a",
          color: connected ? "#44cc88" : "#ff5555",
        }}>
          {connected ? "Connected" : "Offline"}
        </span>
      </div>

      <div style={styles.tabBar}>
        {tabs.map((t) => (
          <button
            key={t.id}
            style={{
              ...styles.tab,
              background: activeTab === t.id ? "#7c7cff" : "transparent",
              color: activeTab === t.id ? "#fff" : "#888",
              borderColor: activeTab === t.id ? "#7c7cff" : "transparent",
            }}
            onClick={() => setActiveTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div style={styles.content}>
        {activeTab === "general" && (
          <>
            <div style={styles.section}>
              <div style={styles.sectionTitle}>LLM Provider</div>
              <select
                style={styles.select}
                value={provider}
                onChange={(e) => handleProviderChange(e.target.value)}
              >
                {PROVIDERS.map((p) => (
                  <option key={p.id} value={p.id}>{p.label}</option>
                ))}
              </select>
            </div>
            <div style={styles.section}>
              <div style={styles.sectionTitle}>Model</div>
              <select
                style={styles.select}
                value={model}
                onChange={(e) => handleModelChange(e.target.value)}
              >
                {PROVIDER_MODELS[provider]?.map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            </div>
            <div style={styles.section}>
              <div style={styles.sectionTitle}>Test Connection</div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {PROVIDERS.map((p) => (
                  <button
                    key={p.id}
                    style={styles.testBtn}
                    onClick={() => handleTestConnection(p.id)}
                    disabled={testing === p.id}
                  >
                    {testing === p.id ? "..." : p.label}
                  </button>
                ))}
              </div>
              {providerTestResult && (
                <div style={styles.testResult}>{providerTestResult}</div>
              )}
            </div>
            <div style={styles.section}>
              <div style={styles.sectionTitle}>System Prompt</div>
              <textarea
                style={styles.textarea}
                rows={4}
                value={systemPrompt}
                onChange={(e) => setSystemPrompt(e.target.value)}
                placeholder="Loading system prompt..."
              />
              <button style={{ ...styles.btn, marginTop: 8 }} onClick={handleSavePrompt}>
                Update Prompt
              </button>
            </div>
            <div style={styles.section}>
              <div style={styles.sectionTitle}>Session Cost</div>
              <button style={styles.btn} onClick={handleFetchCost}>Refresh Cost</button>
              {cost && (
                <div style={styles.costBox}>
                  <div>Total: ${cost.total_cost.toFixed(4)} ({cost.session_calls} calls)</div>
                  <div style={{ fontSize: 11, color: "#888" }}>Input: {cost.total_input_tokens} tok / Output: {cost.total_output_tokens} tok</div>
                  {Object.entries(cost.by_provider).slice(0, 5).map(([p, d]) => (
                    <div key={p} style={{ fontSize: 11, color: "#aaa" }}>{p}: {d.calls} calls, ${d.cost.toFixed(4)}</div>
                  ))}
                </div>
              )}
            </div>
            <div style={styles.section}>
              <div style={styles.sectionTitle}>Agent Features ({FEATURES.length})</div>
              <div style={styles.featureGrid}>
                {FEATURES.map((f) => (
                  <div key={f} style={styles.featureChip}>{f}</div>
                ))}
              </div>
            </div>
          </>
        )}

        {activeTab === "safety" && (
          <>
            <div style={styles.section}>
              <div style={styles.sectionTitle}>Safety Mode</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {SAFETY_MODES.map((m) => (
                  <button
                    key={m.id}
                    style={{
                      ...styles.safetyBtn,
                      background: safetyMode === m.id ? "#1a2a4a" : "#1e1e36",
                      borderColor: safetyMode === m.id ? "#4488cc" : "#3a3a5c",
                    }}
                    onClick={() => handleSafetyChange(m.id)}
                  >
                    <div style={{ fontWeight: 600, fontSize: 13, color: safetyMode === m.id ? "#66aaff" : "#ccc" }}>
                      {m.label}
                      {safetyMode === m.id && <span style={{ marginLeft: 8, fontSize: 10, color: "#44cc88" }}>ACTIVE</span>}
                    </div>
                    <div style={{ fontSize: 11, color: "#888", marginTop: 2 }}>{m.desc}</div>
                  </button>
                ))}
              </div>
            </div>
            <div style={styles.section}>
              <div style={styles.sectionTitle}>Pending Approvals</div>
              <PendingApprovals />
            </div>
          </>
        )}

        {activeTab === "checkpoints" && (
          <>
            <div style={styles.section}>
              <div style={styles.sectionTitle}>Git Checkpoints</div>
              <button style={styles.btn} onClick={handleCreateCheckpoint}>
                + Create Checkpoint
              </button>
              <div style={{ marginTop: 12 }}>
                {checkpoints.length === 0 && (
                  <div style={{ color: "#555", fontSize: 13 }}>No checkpoints yet. Create one to save current state.</div>
                )}
                {checkpoints.map((cp) => (
                  <div key={cp.id} style={styles.checkpointCard}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                      <div>
                        <div style={{ fontWeight: 600, fontSize: 13, color: "#ccc" }}>{cp.label}</div>
                        <div style={{ fontSize: 11, color: "#888" }}>
                          {new Date(cp.timestamp * 1000).toLocaleString()} | {cp.filesChanged} files
                        </div>
                      </div>
                      <button style={styles.rollbackBtn} onClick={() => handleRollback(cp.id)}>
                        Rollback
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </>
        )}

        {activeTab === "index" && (
          <>
            <div style={styles.section}>
              <div style={styles.sectionTitle}>Codebase Index</div>
              {indexStats ? (
                <div style={styles.costBox}>
                  <div>Files indexed: {indexStats.totalFiles}</div>
                  <div>Chunks: {indexStats.totalChunks}</div>
                  <div style={{ fontSize: 11, color: "#888" }}>
                    Last updated: {indexStats.lastUpdated ? new Date(indexStats.lastUpdated * 1000).toLocaleString() : "Never"}
                  </div>
                </div>
              ) : (
                <div style={{ color: "#555", fontSize: 13 }}>Index not built yet.</div>
              )}
              <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
                <button style={styles.btn} onClick={handleReindex}>Reindex</button>
              </div>
            </div>
            <div style={styles.section}>
              <div style={styles.sectionTitle}>Search Codebase</div>
              <div style={{ display: "flex", gap: 8 }}>
                <input
                  style={{ ...styles.select, flex: 1 }}
                  value={indexQuery}
                  onChange={(e) => setIndexQuery(e.target.value)}
                  placeholder="Search for functions, classes, patterns..."
                  onKeyDown={(e) => e.key === "Enter" && handleIndexSearch()}
                />
                <button style={styles.btn} onClick={handleIndexSearch}>Search</button>
              </div>
              {indexResults.length > 0 && (
                <div style={{ marginTop: 10 }}>
                  {indexResults.map((r, i) => (
                    <div key={i} style={styles.searchResult}>{r}</div>
                  ))}
                </div>
              )}
            </div>
          </>
        )}

        {activeTab === "hooks" && (
          <div style={styles.section}>
            <div style={styles.sectionTitle}>Lifecycle Hooks (last 100)</div>
            {hookLog.length === 0 && (
              <div style={{ color: "#555", fontSize: 13 }}>No hook events yet.</div>
            )}
            {hookLog.slice().reverse().map((e, i) => (
              <div key={i} style={styles.hookEvent}>
                <span style={styles.hookBadge}>{e.hook}</span>
                <span style={{ fontSize: 11, color: "#888" }}>
                  {new Date(e.timestamp * 1000).toLocaleTimeString()}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function PendingApprovals() {
  const pendingApprovals = useStore((s) => s.pendingApprovals);
  const { sendCommand } = useWs();

  const handleApprove = (id: string) => {
    sendCommand(`suggest:confirm:${id}`);
  };

  const handleDeny = (id: string) => {
    sendCommand(`suggest:deny:${id}`);
  };

  if (pendingApprovals.length === 0) {
    return <div style={{ color: "#555", fontSize: 13 }}>No pending approvals.</div>;
  }

  return (
    <div>
      {pendingApprovals.map((a) => (
        <div key={a.id} style={styles.approvalCard}>
          <div style={{ fontWeight: 600, fontSize: 13, color: "#ffaa44" }}>{a.tool}</div>
          <div style={{ fontSize: 11, color: "#888", marginTop: 4, fontFamily: "'JetBrains Mono', monospace" }}>
            {JSON.stringify(a.args).slice(0, 200)}
          </div>
          <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
            <button style={{ ...styles.btn, background: "#1a3a2a", color: "#44cc88", borderColor: "#2a5a3a" }} onClick={() => handleApprove(a.id)}>
              Approve
            </button>
            <button style={{ ...styles.btn, background: "#3a1a1a", color: "#ff5555", borderColor: "#5a2a2a" }} onClick={() => handleDeny(a.id)}>
              Deny
            </button>
          </div>
        </div>
      ))}
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
  tabBar: {
    display: "flex",
    padding: "0 16px",
    borderBottom: "1px solid #2a2a3e",
    gap: 2,
  },
  tab: {
    padding: "8px 14px",
    borderRadius: "6px 6px 0 0",
    border: "1px solid transparent",
    borderBottom: "none",
    cursor: "pointer",
    fontSize: 12,
    fontWeight: 500,
    background: "transparent",
    color: "#888",
  },
  content: { flex: 1, overflowY: "auto", padding: 24 },
  section: { marginBottom: 24 },
  sectionTitle: {
    fontSize: 12, fontWeight: 600, color: "#888",
    marginBottom: 10, textTransform: "uppercase", letterSpacing: 1,
  },
  select: {
    width: "100%",
    padding: "9px 14px",
    borderRadius: 10,
    border: "1px solid #3a3a5c",
    background: "#1e1e36",
    color: "#e0e0e0",
    fontSize: 14,
    outline: "none",
  },
  btn: {
    padding: "6px 14px", borderRadius: 6, border: "1px solid #3a3a5c",
    background: "#252540", color: "#bbb", cursor: "pointer", fontSize: 12,
  },
  featureGrid: {
    display: "flex",
    flexWrap: "wrap",
    gap: 6,
  },
  featureChip: {
    padding: "4px 10px",
    borderRadius: 6,
    background: "#252540",
    border: "1px solid #3a3a5c",
    fontSize: 11,
    color: "#aaa",
  },
  testBtn: {
    padding: "4px 10px", borderRadius: 6, border: "1px solid #3a3a5c",
    background: "#252540", color: "#7c7cff", cursor: "pointer", fontSize: 11,
  },
  testResult: {
    marginTop: 8, padding: "6px 10px", borderRadius: 6,
    background: "#1a1a30", border: "1px solid #3a3a5c",
    fontSize: 11, color: "#aaa", fontFamily: "'JetBrains Mono', monospace",
  },
  textarea: {
    width: "100%", padding: "9px 14px", borderRadius: 10,
    border: "1px solid #3a3a5c", background: "#1e1e36",
    color: "#e0e0e0", fontSize: 13, outline: "none",
    fontFamily: "'JetBrains Mono', monospace", resize: "vertical",
  },
  costBox: {
    marginTop: 8, padding: "10px 14px", borderRadius: 8,
    background: "#1a1a30", border: "1px solid #3a3a5c",
    fontSize: 13, color: "#ccc", lineHeight: 1.6,
  },
  safetyBtn: {
    padding: "12px 16px",
    borderRadius: 8,
    border: "1px solid #3a3a5c",
    background: "#1e1e36",
    cursor: "pointer",
    textAlign: "left" as const,
  },
  checkpointCard: {
    padding: "10px 14px",
    borderRadius: 8,
    background: "#1a1a30",
    border: "1px solid #3a3a5c",
    marginBottom: 8,
  },
  rollbackBtn: {
    padding: "4px 12px",
    borderRadius: 6,
    border: "1px solid #5a3a2a",
    background: "#3a2a1a",
    color: "#ffaa44",
    cursor: "pointer",
    fontSize: 11,
  },
  approvalCard: {
    padding: "10px 14px",
    borderRadius: 8,
    background: "#2a2a1a",
    border: "1px solid #5a5a3a",
    marginBottom: 8,
  },
  searchResult: {
    padding: "6px 10px",
    borderRadius: 6,
    background: "#1a1a30",
    border: "1px solid #3a3a5c",
    fontSize: 12,
    color: "#aaa",
    fontFamily: "'JetBrains Mono', monospace",
    marginBottom: 4,
  },
  hookEvent: {
    display: "flex",
    alignItems: "center",
    gap: 10,
    padding: "4px 0",
    borderBottom: "1px solid #1a1a2a",
  },
  hookBadge: {
    padding: "2px 8px",
    borderRadius: 4,
    background: "#252540",
    border: "1px solid #3a3a5c",
    fontSize: 10,
    color: "#7c7cff",
    fontFamily: "'JetBrains Mono', monospace",
  },
};
