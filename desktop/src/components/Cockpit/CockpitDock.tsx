import React, { useEffect, useRef, useState } from "react";
import { useStore } from "../../store/session";
import { useWs } from "../../hooks/useWebSocket";

/** Bottom dock of the Cockpit: live app Preview (canvas) + sandbox Terminal
 *  fed by the agent's command/file stream. */

export default function CockpitDock() {
  const [open, setOpen] = useState(true);
  const [tab, setTab] = useState<"preview" | "terminal">("terminal");
  const [url, setUrl] = useState("http://localhost:5173");
  const [frameKey, setFrameKey] = useState(0);
  const [cmd, setCmd] = useState("");
  const stream = useStore((s) => s.stream);
  const setStream = useStore((s) => s.setStream);
  const { sendCommand } = useWs();
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => endRef.current?.scrollIntoView({ behavior: "smooth" }), [stream]);

  const runCmd = () => {
    if (!cmd.trim()) return;
    sendCommand(`bg:start:${cmd.trim()}`);
    setCmd("");
    setTab("terminal");
  };

  return (
    <div style={{ borderTop: "1px solid #2a2a3e", display: "flex", flexDirection: "column", height: open ? 220 : 30, flexShrink: 0, transition: "height 0.15s" }}>
      <div style={{ height: 30, display: "flex", alignItems: "center", gap: 4, padding: "0 8px", flexShrink: 0 }}>
        {(["terminal", "preview"] as const).map((t) => (
          <button key={t} onClick={() => { setTab(t); setOpen(true); }} style={{
            ...tabBtn, background: open && tab === t ? "#2d2d3d" : "transparent",
            color: open && tab === t ? "#d5d5ea" : "#8a8aa5",
          }}>{t === "terminal" ? "⌨ Terminal" : "▶ Preview"}</button>
        ))}
        <span style={{ flex: 1 }} />
        {open && tab === "terminal" && (
          <button style={tabBtn} onClick={() => setStream([])}>clear</button>
        )}
        {open && tab === "preview" && (
          <>
            <input value={url} onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && setFrameKey(frameKey + 1)}
              style={{ width: 240, padding: "2px 8px", borderRadius: 6, border: "1px solid #3a3a5c", background: "#16162a", color: "#c5c5da", fontSize: 11 }} />
            <button style={tabBtn} onClick={() => setFrameKey(frameKey + 1)}>↻</button>
          </>
        )}
        <button style={tabBtn} onClick={() => setOpen(!open)}>{open ? "▾" : "▴"}</button>
      </div>
      {open && tab === "terminal" && (
        <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
          <div style={{ flex: 1, overflow: "auto", padding: "4px 10px", fontFamily: "'JetBrains Mono', monospace", fontSize: 11.5, lineHeight: 1.5 }}>
            {stream.length === 0 && <div style={{ color: "#555" }}>Agent command output and file events stream here. Run something below or ask the agent.</div>}
            {stream.slice(-400).map((e: any, i: number) => (
              <div key={i} style={{ color: e.type === "file_written" || e.type === "file_edited" ? "#7c7cff" : "#a8c8a8", whiteSpace: "pre-wrap" }}>
                {e.line || (e.type === "file_written" ? `✎ wrote ${e.filepath}` : e.type === "file_edited" ? `✎ edited ${e.filepath}` : JSON.stringify(e))}
              </div>
            ))}
            <div ref={endRef} />
          </div>
          <div style={{ display: "flex", gap: 6, padding: 6, borderTop: "1px solid #22223a" }}>
            <span style={{ color: "#7c7cff", fontFamily: "'JetBrains Mono', monospace", fontSize: 12, alignSelf: "center" }}>$</span>
            <input value={cmd} onChange={(e) => setCmd(e.target.value)} onKeyDown={(e) => e.key === "Enter" && runCmd()}
              placeholder="run a command in the sandbox (background)…"
              style={{ flex: 1, padding: "4px 8px", borderRadius: 6, border: "1px solid #3a3a5c", background: "#16162a", color: "#d5d5ea", fontSize: 12, fontFamily: "'JetBrains Mono', monospace", outline: "none" }} />
          </div>
        </div>
      )}
      {open && tab === "preview" && (
        <iframe key={frameKey} src={url} title="app preview" style={{ flex: 1, border: "none", background: "#fff" }} />
      )}
    </div>
  );
}

const tabBtn: React.CSSProperties = {
  padding: "2px 10px", borderRadius: 6, border: "none", background: "transparent",
  color: "#8a8aa5", cursor: "pointer", fontSize: 11,
};
