import React, { useState } from "react";
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

export default function SettingsPanel() {
  const [systemPrompt, setSystemPrompt] = useState("");
  const provider = useStore((s) => s.provider);
  const model = useStore((s) => s.model);
  const connected = useStore((s) => s.connected);
  const cost = useStore((s) => s.cost);
  const providerTestResult = useStore((s) => s.providerTestResult);
  const setProvider = useStore((s) => s.setProvider);
  const setModel = useStore((s) => s.setModel);
  const { switchProvider, sendCommand, subscribe } = useWs();
  const [testing, setTesting] = useState<string | null>(null);

  const handleProviderChange = (p: string) => {
    setProvider(p);
    switchProvider(p);
  };

  const handleTestConnection = async (p: string) => {
    setTesting(p);
    sendCommand(`provider:test:${p}`);
    setTimeout(() => setTesting(null), 5000);
  };

  const handleFetchCost = () => sendCommand("cost");

  React.useEffect(() => {
    const unsub = subscribe("system_prompt", (data: unknown) => {
      setSystemPrompt((data as any).data || "");
    });
    sendCommand("system_prompt");
    return unsub;
  }, [subscribe, sendCommand]);

  const handleSavePrompt = () => {
    sendCommand(`system_prompt:set:${systemPrompt}`);
  };

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
      <div style={styles.content}>
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
            onChange={(e) => setModel(e.target.value)}
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
};
