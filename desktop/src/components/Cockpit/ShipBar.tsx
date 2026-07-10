import React, { useState } from "react";
import { useWs } from "../../hooks/useWebSocket";

/** One-click Ship bar: scaffold a project (text-to-app) or plan a deploy,
 *  without typing slash commands. Results land in the chat as notifications. */

const KINDS = ["web", "tailwind", "shadcn", "supabase", "api", "saas", "fullstack",
  "astro", "svelte", "cli", "mobile", "mcp"];
const TARGETS = ["vercel", "netlify", "fly", "eas"];

export default function ShipBar() {
  const { sendCommand } = useWs();
  const [kind, setKind] = useState("tailwind");
  const [name, setName] = useState("");
  const [target, setTarget] = useState("vercel");
  const [dir, setDir] = useState("");

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6, padding: "5px 10px", borderBottom: "1px solid #2a2a3e", flexShrink: 0, flexWrap: "wrap" }}>
      <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: 0.8, color: "#7c7cff" }}>SHIP</span>
      <select value={kind} onChange={(e) => setKind(e.target.value)} style={sel}>
        {KINDS.map((k) => <option key={k} value={k}>{k}</option>)}
      </select>
      <input value={name} onChange={(e) => setName(e.target.value)} placeholder="app name"
        onKeyDown={(e) => e.key === "Enter" && name.trim() && sendCommand(`scaffold:${kind}:${name.trim()}`)}
        style={{ ...inp, width: 130 }} />
      <button style={btn} disabled={!name.trim()}
        onClick={() => name.trim() && sendCommand(`scaffold:${kind}:${name.trim()}`)}>
        ⚡ Scaffold
      </button>
      <span style={{ width: 1, height: 18, background: "#2a2a3e", margin: "0 4px" }} />
      <select value={target} onChange={(e) => setTarget(e.target.value)} style={sel}>
        {TARGETS.map((t) => <option key={t} value={t}>{t}</option>)}
      </select>
      <input value={dir} onChange={(e) => setDir(e.target.value)} placeholder="project dir (.)"
        style={{ ...inp, width: 110 }} />
      <button style={{ ...btn, borderColor: "#4a4a2f", color: "#ffcc66" }}
        title="Run the eval gate (build/compile/tests/MCP handshake) before shipping"
        onClick={() => sendCommand(`verify:${dir.trim()}`)}>
        ✓ Verify
      </button>
      <button style={{ ...btn, borderColor: "#2f6e4a", color: "#66dd99" }}
        onClick={() => sendCommand(`deploy:${target}:${dir.trim()}`)}>
        🚀 Deploy plan
      </button>
    </div>
  );
}

const sel: React.CSSProperties = { padding: "3px 6px", borderRadius: 6, border: "1px solid #3a3a5c", background: "#16162a", color: "#c5c5da", fontSize: 11.5 };
const inp: React.CSSProperties = { padding: "3px 8px", borderRadius: 6, border: "1px solid #3a3a5c", background: "#16162a", color: "#d5d5ea", fontSize: 11.5, outline: "none" };
const btn: React.CSSProperties = { padding: "3px 12px", borderRadius: 6, border: "1px solid #3a3a5c", background: "#252540", color: "#c0c0d8", cursor: "pointer", fontSize: 11.5 };
