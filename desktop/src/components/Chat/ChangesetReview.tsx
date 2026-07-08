import React, { useEffect, useState } from "react";
import { useWs } from "../../hooks/useWebSocket";
import { ChangesetSummary } from "../../types";

/** Per-file diff review rendered under an assistant message that edited files.
 *  Accept keeps the applied edit; Reject restores the original content. */

const STATUS_COLOR: Record<string, string> = {
  applied: "#ffcc66", accepted: "#66dd99", reverted: "#ff8877", partial: "#7c7cff",
};

function DiffBlock({ diff }: { diff: string }) {
  return (
    <pre style={{
      margin: 0, padding: 10, fontSize: 11.5, lineHeight: 1.45, overflow: "auto",
      maxHeight: 260, fontFamily: "'JetBrains Mono', monospace", background: "#16162a",
      borderRadius: 6,
    }}>
      {diff.split("\n").map((line, i) => (
        <div key={i} style={{
          color: line.startsWith("+") ? "#66dd99" : line.startsWith("-") ? "#ff8877"
               : line.startsWith("@@") ? "#7c7cff" : "#8a8aa5",
        }}>{line}</div>
      ))}
    </pre>
  );
}

export default function ChangesetReview({ changeset }: { changeset: ChangesetSummary }) {
  const { sendCommand, subscribe } = useWs();
  const [cs, setCs] = useState(changeset);
  const [open, setOpen] = useState<Record<string, boolean>>({});

  useEffect(() => {
    return subscribe("changeset_update", (m: any) => {
      if (m.data?.id === cs.id && m.data.files) setCs({ id: m.data.id, files: m.data.files });
    });
  }, [subscribe, cs.id]);

  if (!cs.files.length) return null;

  return (
    <div style={{
      marginTop: 8, border: "1px solid #3a3a5c", borderRadius: 10, overflow: "hidden",
    }}>
      <div style={{ padding: "6px 10px", fontSize: 11, fontWeight: 700, letterSpacing: 0.6, color: "#7c7cff", background: "#1e1e34" }}>
        CHANGESET {cs.id} · {cs.files.length} file{cs.files.length > 1 ? "s" : ""}
      </div>
      {cs.files.map((f) => (
        <div key={f.path} style={{ borderTop: "1px solid #2a2a3e" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 10px" }}>
            <span onClick={() => setOpen({ ...open, [f.path]: !open[f.path] })}
              style={{ cursor: "pointer", fontSize: 12, fontFamily: "'JetBrains Mono', monospace", color: "#d5d5ea", flex: 1 }}>
              {open[f.path] ? "▾" : "▸"} {f.path}
            </span>
            <span style={{ fontSize: 10, fontWeight: 700, color: STATUS_COLOR[f.status] || "#999" }}>
              {f.status.toUpperCase()}
            </span>
            {f.status === "applied" && (
              <>
                <button style={{ ...miniBtn, borderColor: "#2f6e4a", color: "#66dd99" }}
                  onClick={() => sendCommand(`changeset:accept:${cs.id}:${f.path}`)}>Accept</button>
                <button style={{ ...miniBtn, borderColor: "#6e2f2f", color: "#ff8877" }}
                  onClick={() => sendCommand(`changeset:reject:${cs.id}:${f.path}`)}>Reject</button>
              </>
            )}
          </div>
          {open[f.path] && (
            <div style={{ padding: "0 10px 10px", display: "flex", flexDirection: "column", gap: 6 }}>
              {(f.hunks && f.hunks.length > 1 ? f.hunks : null)?.map((h) => (
                <div key={h.index} style={{ border: "1px solid #2a2a3e", borderRadius: 8, overflow: "hidden" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "3px 8px", background: "#1e1e34" }}>
                    <span style={{ fontSize: 10.5, color: "#9a9ab8", flex: 1 }}>hunk {h.index + 1}/{f.hunks!.length}</span>
                    <span style={{ fontSize: 9.5, fontWeight: 700, color: STATUS_COLOR[h.status] || "#999" }}>
                      {h.status.toUpperCase()}
                    </span>
                    {h.status === "applied" && (
                      <>
                        <button style={{ ...miniBtn, borderColor: "#2f6e4a", color: "#66dd99" }}
                          onClick={() => sendCommand(`changeset:accept_hunk:${cs.id}:${h.index}:${f.path}`)}>✓</button>
                        <button style={{ ...miniBtn, borderColor: "#6e2f2f", color: "#ff8877" }}
                          onClick={() => sendCommand(`changeset:reject_hunk:${cs.id}:${h.index}:${f.path}`)}>✗</button>
                      </>
                    )}
                  </div>
                  <DiffBlock diff={h.diff} />
                </div>
              )) || <DiffBlock diff={f.diff} />}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

const miniBtn: React.CSSProperties = {
  padding: "2px 10px", borderRadius: 6, border: "1px solid", background: "transparent",
  cursor: "pointer", fontSize: 11,
};
