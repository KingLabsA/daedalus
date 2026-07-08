import React, { useState, useRef, useEffect } from "react";
import ReactMarkdown from "react-markdown";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import { useStore } from "../../store/session";
import { useAgent } from "../../hooks/useAgent";
import { useWs } from "../../hooks/useWebSocket";
import ChangesetReview from "./ChangesetReview";

const SLASH_COMMANDS: Record<string, string> = {
  "/help": "Show this help",
  "/map": "Map repository structure",
  "/cost": "Show session cost breakdown",
  "/logs": "Show agent logs",
  "/kanban": "Show kanban board",
  "/undo": "Undo last git changes",
  "/provider:name": "Switch provider (e.g. /provider:anthropic)",
  "/test:name": "Test a provider connection (e.g. /test:openai)",
  "/diff": "Show git diff preview",
  "/status": "Show git status",
  "/files": "Show file explorer",
  "/branches": "Show git branches",
  "/log": "Show git log",
};

export default function ChatView() {
  const messages = useStore((s) => s.messages);
  const connected = useStore((s) => s.connected);
  const connecting = useStore((s) => s.connecting);
  const streamingContent = useStore((s) => s.streamingContent);
  const streamingMessageId = useStore((s) => s.streamingMessageId);
  const [input, setInput] = useState("");
  const [showHelp, setShowHelp] = useState(false);
  const [imageData, setImageData] = useState<string | null>(null);
  const { send, loading } = useAgent();
  const { command } = useWs();
  const endRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => endRef.current?.scrollIntoView({ behavior: "smooth" }), [messages, streamingContent]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        document.querySelector<HTMLInputElement>('[data-chat-input]')?.focus();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  const handleSend = () => {
    if (!input.trim() || loading) return;
    const text = input.trim();
    if (text.startsWith("/")) {
      const [cmd, ...args] = text.slice(1).split(" ");
      const full = text.slice(1);
      if (full.startsWith("provider:") && full.includes(":")) {
        const p = full.split(":")[1];
        command(`provider:${p}`);
        useStore.getState().addMessage({ id: Date.now().toString(), role: "user", content: text, toolCalls: [], timestamp: new Date() });
        useStore.getState().addMessage({ id: (Date.now()+1).toString(), role: "assistant", content: `Switched provider to ${p}`, toolCalls: [], timestamp: new Date() });
      } else if (full.startsWith("test:")) {
        const p = full.split(":")[1];
        command(`provider:test:${p}`);
        useStore.getState().addMessage({ id: Date.now().toString(), role: "user", content: text, toolCalls: [], timestamp: new Date() });
      } else if (cmd === "help") {
        const helpText = Object.entries(SLASH_COMMANDS).map(([k, v]) => `${k} — ${v}`).join("\n");
        useStore.getState().addMessage({ id: Date.now().toString(), role: "user", content: text, toolCalls: [], timestamp: new Date() });
        useStore.getState().addMessage({ id: (Date.now()+1).toString(), role: "assistant", content: `## Available Commands\n\n${helpText}`, toolCalls: [], timestamp: new Date() });
      } else if (["map", "cost", "logs", "kanban", "undo", "diff", "status", "files"].includes(cmd)) {
        command(full);
        useStore.getState().addMessage({ id: Date.now().toString(), role: "user", content: text, toolCalls: [], timestamp: new Date() });
        useStore.getState().addMessage({ id: (Date.now()+1).toString(), role: "assistant", content: `Running: ${full}...`, toolCalls: [], timestamp: new Date() });
      } else if (cmd === "branches") {
        command("git_branches");
        useStore.getState().addMessage({ id: Date.now().toString(), role: "user", content: text, toolCalls: [], timestamp: new Date() });
      } else if (cmd === "log") {
        command("git_log");
        useStore.getState().addMessage({ id: Date.now().toString(), role: "user", content: text, toolCalls: [], timestamp: new Date() });
      } else {
        send(text);
      }
    } else {
      if (imageData) {
        send(text + "\n\n[Image attached]");
      } else {
        send(text);
      }
    }
    setInput("");
    setImageData(null);
  };

  const handleImagePick = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => setImageData(reader.result as string);
    reader.readAsDataURL(file);
  };

  return (
    <div style={styles.container}>
      <div style={styles.header}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ fontWeight: 600, fontSize: 14 }}>Chat</span>
          <div style={{
            width: 6, height: 6, borderRadius: "50%", display: "inline-block",
            background: connected ? "#44cc88" : connecting ? "#ffaa44" : "#ff5555",
          }} />
          <span style={{ fontSize: 11, color: "#777" }}>
            {connected ? "Connected" : connecting ? "Connecting..." : "Offline"}
          </span>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <button onClick={() => setShowHelp(!showHelp)} style={styles.smallBtn}>/</button>
          <span style={{ fontSize: 11, color: "#7c7cff" }}>
            {useStore.getState().provider} / {useStore.getState().model}
          </span>
        </div>
      </div>
      <div style={styles.messages}>
        {showHelp && (
          <div style={styles.helpBox}>
            {Object.entries(SLASH_COMMANDS).map(([k, v]) => (
              <div key={k} style={{ fontSize: 12, color: "#aaa", padding: "2px 0" }}>
                <span style={{ color: "#7c7cff", fontFamily: "'JetBrains Mono', monospace", marginRight: 8 }}>{k}</span>
                {v}
              </div>
            ))}
          </div>
        )}
        {messages.length === 0 && (
          <div style={styles.empty}>
            <div style={styles.emptyIcon}>HU</div>
            <div style={{ fontWeight: 600, fontSize: 16, color: "#888" }}>
              {connected ? "Ready" : "Hermes-Ultimate"}
            </div>
            <div style={{ fontSize: 13, color: "#555", marginTop: 4 }}>
              {connected ? 'Send a message or type /help' : 'Waiting for agent...'}
            </div>
          </div>
        )}
        {messages.map((m) => (
          <div
            key={m.id}
            style={{
              ...styles.msg,
              alignSelf: m.role === "user" ? "flex-end" : "flex-start",
              background: m.role === "user" ? "#3b3b5c" : "#252540",
              maxWidth: "80%",
            }}
          >
            <div style={{ fontSize: 11, color: "#666", marginBottom: 4 }}>
              {m.role === "user" ? "You" : "Assistant"}
            </div>
            <div style={{ lineHeight: 1.5, fontSize: 14 }}>
              {m.role === "assistant" ? (
                <ReactMarkdown
                  components={{
                    code({ className, children, ...props }) {
                      const match = /language-(\w+)/.exec(className || "");
                      if (match) {
                        return (
                          <SyntaxHighlighter style={oneDark} language={match[1]} PreTag="div">
                            {String(children).replace(/\n$/, "")}
                          </SyntaxHighlighter>
                        );
                      }
                      return <code style={{ background: "#2a2a3e", padding: "2px 6px", borderRadius: 4, fontSize: 13 }} {...props}>{children}</code>;
                    },
                  }}
                >
                  {m.content}
                </ReactMarkdown>
              ) : (
                <span style={{ whiteSpace: "pre-wrap" }}>{m.content}</span>
              )}
            </div>
            {m.toolCalls?.map((tc, i) => (
              <div key={i} style={styles.toolBadge}>
                {tc.name}({JSON.stringify(tc.args)})
              </div>
            ))}
            {m.changeset && <ChangesetReview changeset={m.changeset} />}
            {m.routedTo && (
              <div style={{ fontSize: 10, color: "#7c7cff", marginTop: 4, opacity: 0.75 }}>
                routed → {m.routedTo}
              </div>
            )}
          </div>
        ))}
        {streamingMessageId && streamingContent && (
          <div style={{ ...styles.msg, alignSelf: "flex-start", background: "#252540", maxWidth: "80%" }}>
            <div style={{ fontSize: 11, color: "#666", marginBottom: 4 }}>Assistant</div>
            <div style={{ lineHeight: 1.5, fontSize: 14 }}>
              <ReactMarkdown
                components={{
                  code({ className, children, ...props }) {
                    const match = /language-(\w+)/.exec(className || "");
                    if (match) {
                      return (
                        <SyntaxHighlighter style={oneDark} language={match[1]} PreTag="div">
                          {String(children).replace(/\n$/, "")}
                        </SyntaxHighlighter>
                      );
                    }
                    return <code style={{ background: "#2a2a3e", padding: "2px 6px", borderRadius: 4, fontSize: 13 }} {...props}>{children}</code>;
                  },
                }}
              >
                {streamingContent}
              </ReactMarkdown>
              <span style={{ animation: "blink 1s infinite", color: "#7c7cff" }}>|</span>
            </div>
          </div>
        )}
        <div ref={endRef} />
      </div>
      {imageData && (
        <div style={styles.imagePreview}>
          <img src={imageData} alt="preview" style={{ maxHeight: 80, borderRadius: 6 }} />
          <button onClick={() => setImageData(null)} style={styles.removeImgBtn}>x</button>
        </div>
      )}
      <div style={styles.inputRow}>
        <input
          ref={fileRef}
          type="file"
          accept="image/*"
          style={{ display: "none" }}
          onChange={handleImagePick}
        />
        <button onClick={() => fileRef.current?.click()} style={styles.imgBtn} title="Attach image">
          +
        </button>
        <input
          style={styles.input}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend(); }
          }}
          data-chat-input="true"
          placeholder={connected ? "Message, /command, or @file... (⌘K)" : "Agent offline"}
          disabled={loading || !connected}
        />
        {loading ? (
          <button style={{ ...styles.sendBtn, background: "#a03d3d" }}
            onClick={() => command("cancel")} title="Stop the current run">
            Stop
          </button>
        ) : (
          <button style={{ ...styles.sendBtn, opacity: !connected ? 0.5 : 1 }}
            onClick={handleSend} disabled={!connected}>
            Send
          </button>
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
  messages: {
    flex: 1,
    overflowY: "auto",
    padding: 16,
    display: "flex",
    flexDirection: "column",
    gap: 12,
  },
  empty: {
    flex: 1,
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
  },
  emptyIcon: {
    fontSize: 28,
    fontWeight: 700,
    fontFamily: "'JetBrains Mono', monospace",
    color: "#3a3a5c",
    marginBottom: 12,
    letterSpacing: 3,
  },
  msg: {
    padding: "10px 14px",
    borderRadius: 12,
    fontSize: 14,
    wordBreak: "break-word",
  },
  toolBadge: {
    fontSize: 11,
    color: "#7c7cff",
    background: "rgba(124,124,255,0.08)",
    padding: "2px 8px",
    borderRadius: 6,
    marginTop: 6,
    fontFamily: "'JetBrains Mono', monospace",
  },
  inputRow: {
    display: "flex",
    padding: "10px 16px",
    gap: 8,
    borderTop: "1px solid #2a2a3e",
  },
  input: {
    flex: 1,
    padding: "10px 14px",
    borderRadius: 10,
    border: "1px solid #3a3a5c",
    background: "#1e1e36",
    color: "#e0e0e0",
    fontSize: 14,
    outline: "none",
  },
  sendBtn: {
    padding: "10px 18px",
    borderRadius: 10,
    border: "none",
    background: "#7c7cff",
    color: "#fff",
    fontSize: 13,
    fontWeight: 600,
    cursor: "pointer",
  },
  helpBox: {
    background: "#1a1a30",
    border: "1px solid #3a3a5c",
    borderRadius: 10,
    padding: 12,
    marginBottom: 8,
  },
  smallBtn: {
    background: "transparent",
    border: "1px solid #3a3a5c",
    color: "#7c7cff",
    borderRadius: 6,
    padding: "2px 8px",
    fontSize: 13,
    cursor: "pointer",
  },
  imagePreview: {
    padding: "0 16px",
    display: "flex",
    gap: 8,
    alignItems: "center",
  },
  removeImgBtn: {
    background: "transparent",
    border: "1px solid #ff5555",
    color: "#ff5555",
    borderRadius: 4,
    padding: "2px 6px",
    fontSize: 11,
    cursor: "pointer",
  },
  imgBtn: {
    background: "transparent",
    border: "1px solid #3a3a5c",
    color: "#aaa",
    borderRadius: 6,
    padding: "6px 10px",
    fontSize: 16,
    cursor: "pointer",
  },
};
