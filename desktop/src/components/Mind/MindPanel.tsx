import React, { useEffect, useState, useCallback } from "react";
import { useWs } from "../../hooks/useWebSocket";
import { useStore } from "../../store/session";

/** Deep Mind dashboard — memory, subconscious, calibration, experts (MoE),
 *  blast radius, device doctor, and the hardware model advisor. */

function Section({ title, children, actions }: { title: string; children: React.ReactNode; actions?: React.ReactNode }) {
  const theme = useStore((s) => s.theme);
  const isDark = theme === "dark";
  return (
    <div style={{
      background: isDark ? "#1e1e34" : "#ffffff",
      border: `1px solid ${isDark ? "#2a2a3e" : "#d0d0dc"}`,
      borderRadius: 12, padding: 16, display: "flex", flexDirection: "column", gap: 10,
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: 0.8, color: "#7c7cff", textTransform: "uppercase" }}>{title}</div>
        <div style={{ display: "flex", gap: 6 }}>{actions}</div>
      </div>
      {children}
    </div>
  );
}

function Btn({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button onClick={onClick} style={{
      padding: "4px 12px", borderRadius: 6, border: "1px solid #3a3a5c",
      background: "#252540", color: "#c0c0d8", cursor: "pointer", fontSize: 11,
    }}>{label}</button>
  );
}

function Mono({ children }: { children: React.ReactNode }) {
  return <pre style={{
    margin: 0, fontSize: 11, fontFamily: "'JetBrains Mono', monospace",
    whiteSpace: "pre-wrap", wordBreak: "break-word", color: "#a8a8c0", maxHeight: 220, overflow: "auto",
  }}>{children}</pre>;
}

function Input({ value, onChange, onEnter, placeholder }: {
  value: string; onChange: (v: string) => void; onEnter: () => void; placeholder: string;
}) {
  return (
    <input
      value={value}
      onChange={(e) => onChange(e.target.value)}
      onKeyDown={(e) => e.key === "Enter" && onEnter()}
      placeholder={placeholder}
      style={{
        flex: 1, padding: "6px 10px", borderRadius: 6, border: "1px solid #3a3a5c",
        background: "#16162a", color: "#d0d0e0", fontSize: 12, outline: "none",
      }}
    />
  );
}

export default function MindPanel() {
  const { sendCommand, subscribe, connected } = useWs();

  const [memory, setMemory] = useState<any>(null);
  const [memoryHits, setMemoryHits] = useState<any[] | null>(null);
  const [memQuery, setMemQuery] = useState("");
  const [subconscious, setSubconscious] = useState<any>(null);
  const [dreamReport, setDreamReport] = useState<any>(null);
  const [distillReport, setDistillReport] = useState<any>(null);
  const [calibration, setCalibration] = useState<any>(null);
  const [experts, setExperts] = useState<any>(null);
  const [route, setRoute] = useState<any>(null);
  const [routeQuery, setRouteQuery] = useState("");
  const [blast, setBlast] = useState<any>(null);
  const [blastQuery, setBlastQuery] = useState("");
  const [doctor, setDoctor] = useState<any>(null);
  const [advisor, setAdvisor] = useState<any>(null);
  const [profile, setProfile] = useState<any>(undefined);

  useEffect(() => {
    const subs = [
      subscribe("memory", (m: any) => Array.isArray(m.data) ? setMemoryHits(m.data) : setMemory(m.data)),
      subscribe("subconscious", (m: any) => setSubconscious(m.data)),
      subscribe("dream", (m: any) => setDreamReport(m.data)),
      subscribe("distill", (m: any) => setDistillReport(m.data)),
      subscribe("calibration", (m: any) => setCalibration(m.data)),
      subscribe("experts", (m: any) => setExperts(m.data)),
      subscribe("route", (m: any) => setRoute(m.data)),
      subscribe("blast", (m: any) => setBlast(m.data)),
      subscribe("doctor", (m: any) => setDoctor(m.data)),
      subscribe("advisor", (m: any) => setAdvisor(m.data)),
      subscribe("profile", (m: any) => setProfile(m.data)),
    ];
    return () => subs.forEach((u) => u());
  }, [subscribe]);

  const refreshAll = useCallback(() => {
    ["memory", "subconscious", "calibration", "experts", "doctor", "advisor", "profile"].forEach(sendCommand);
  }, [sendCommand]);

  useEffect(() => { if (connected) refreshAll(); }, [connected, refreshAll]);

  return (
    <div style={{ flex: 1, overflow: "auto", padding: 20 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <div>
          <div style={{ fontSize: 20, fontWeight: 700 }}>Deep Mind</div>
          <div style={{ fontSize: 12, color: "#888" }}>
            Memory · Subconscious · Calibration · Experts · World Model · Doctor · Models
          </div>
        </div>
        <Btn label="Refresh all" onClick={refreshAll} />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(340px, 1fr))", gap: 14 }}>

        <Section title="Persistent Memory" actions={<Btn label="Stats" onClick={() => sendCommand("memory")} />}>
          {memory && (
            <div style={{ fontSize: 12, color: "#a8a8c0" }}>
              {memory.memories} memories · {memory.checkpoints} checkpoints · {memory.failures} failure antibodies
            </div>
          )}
          <div style={{ display: "flex", gap: 6 }}>
            <Input value={memQuery} onChange={setMemQuery} placeholder="search memory…"
              onEnter={() => memQuery && sendCommand(`memory:search:${memQuery}`)} />
            <Btn label="Search" onClick={() => memQuery && sendCommand(`memory:search:${memQuery}`)} />
          </div>
          {memoryHits && (memoryHits.length
            ? <Mono>{memoryHits.map((h: any) => `[${h.kind}] ${h.content}`).join("\n")}</Mono>
            : <div style={{ fontSize: 12, color: "#777" }}>No matches.</div>)}
        </Section>

        <Section title="Subconscious" actions={<>
          <Btn label="Dream now" onClick={() => sendCommand("dream")} />
          <Btn label="Distill now" onClick={() => sendCommand("distill")} />
          <Btn label="Status" onClick={() => sendCommand("subconscious")} />
        </>}>
          {subconscious && (
            <div style={{ fontSize: 12, color: "#a8a8c0" }}>
              {subconscious.enabled ? "enabled" : "disabled"} · {subconscious.running ? "running" : "stopped"} ·
              idle {Math.round(subconscious.idle_for_seconds)}s / {subconscious.idle_threshold_seconds}s ·
              {" "}{subconscious.cycles_last_hour} cycles last hour
            </div>
          )}
          {dreamReport && <Mono>{`dream: ${JSON.stringify(dreamReport)}`}</Mono>}
          {distillReport && <Mono>{`distill: ${JSON.stringify(distillReport)}`}</Mono>}
        </Section>

        <Section title="Calibration (predicted vs actual)" actions={<Btn label="Report" onClick={() => sendCommand("calibration")} />}>
          {calibration && (calibration.total_events
            ? <Mono>{calibration.buckets.map((b: any) =>
                `${b.range}: predicted, actual ${b.actual ?? "?"} (n=${b.n})`).join("\n")
                + "\n" + Object.entries(calibration.by_kind).map(([k, v]: any) => `${k}: ${v.rate} (n=${v.n})`).join("\n")}</Mono>
            : <div style={{ fontSize: 12, color: "#777" }}>No calibration data yet — it accumulates as Hermes works.</div>)}
        </Section>

        <Section title="Experts / MoE Routing" actions={<Btn label="Providers" onClick={() => sendCommand("experts")} />}>
          {experts && (
            <div style={{ fontSize: 12, color: "#a8a8c0" }}>
              live: {(experts.available || []).join(", ") || "none"}
            </div>
          )}
          <div style={{ display: "flex", gap: 6 }}>
            <Input value={routeQuery} onChange={setRouteQuery} placeholder="how would this task route?"
              onEnter={() => routeQuery && sendCommand(`route:${routeQuery}`)} />
            <Btn label="Route" onClick={() => routeQuery && sendCommand(`route:${routeQuery}`)} />
          </div>
          {route && <Mono>{`→ ${route.provider} (tier ${route.tier}, difficulty ${route.difficulty})\n${route.reason}`}</Mono>}
        </Section>

        <Section title="World Model — Blast Radius">
          <div style={{ display: "flex", gap: 6 }}>
            <Input value={blastQuery} onChange={setBlastQuery} placeholder="path/to/file.py"
              onEnter={() => blastQuery && sendCommand(`blast:${blastQuery}`)} />
            <Btn label="Predict" onClick={() => blastQuery && sendCommand(`blast:${blastQuery}`)} />
          </div>
          {blast && (
            <div style={{ fontSize: 12 }}>
              <span style={{
                padding: "2px 8px", borderRadius: 10, fontWeight: 700,
                background: blast.risk >= 0.5 ? "#4d1f1f" : blast.risk >= 0.3 ? "#4d3d1f" : "#1f4d2c",
                color: blast.risk >= 0.5 ? "#ff8877" : blast.risk >= 0.3 ? "#ffcc66" : "#66dd99",
              }}>risk {blast.risk}</span>
              <Mono>{(blast.reasons || []).join("\n") || "low risk"}
                {blast.importers?.length ? `\nimported by: ${blast.importers.join(", ")}` : ""}
                {blast.co_changes?.length ? `\nco-changes: ${blast.co_changes.map((c: any) => `${c[0]} ${Math.round(c[1] * 100)}%`).join(", ")}` : ""}</Mono>
            </div>
          )}
        </Section>

        <Section title="Device Doctor" actions={<Btn label="Scan" onClick={() => sendCommand("doctor")} />}>
          {doctor && (
            <div style={{ fontSize: 12, color: "#a8a8c0" }}>
              <div>{doctor.ok?.length} OK · {doctor.missing?.length} missing · {doctor.providers_live?.length} providers live · {doctor.disk_free_gb} GB free</div>
              {doctor.missing?.length > 0 && (
                <Mono>{doctor.missing.map((m: any) => `MISSING ${m.name} — ${m.needed_for}\n  fix: ${m.install}`).join("\n")}</Mono>
              )}
            </div>
          )}
        </Section>

        <Section title="Model Advisor (this machine)" actions={<Btn label="Advise" onClick={() => sendCommand("advisor")} />}>
          {advisor && (
            <div style={{ fontSize: 12, color: "#a8a8c0" }}>
              <div>
                {advisor.specs?.os}/{advisor.specs?.arch} · {advisor.specs?.ram_gb} GB
                {advisor.specs?.apple_silicon ? " · Apple Silicon" : ""} · usable ~{advisor.usable_memory_gb} GB
              </div>
              <div style={{ marginTop: 4, fontWeight: 700, color: "#7c7cff" }}>
                Recommended: {advisor.recommended?.local || "—"} (local) · {advisor.recommended?.cloud || "—"} (cloud)
              </div>
              <Mono>{(advisor.local_models || []).map((m: any) =>
                `${m.installed ? "✓" : "·"} ${m.model} (~${m.tier_ram_gb}GB)`).join("\n")}</Mono>
            </div>
          )}
        </Section>

        <Section title="Profile" actions={<Btn label="Reload" onClick={() => sendCommand("profile")} />}>
          {profile === undefined && <div style={{ fontSize: 12, color: "#777" }}>Loading…</div>}
          {profile === null && <div style={{ fontSize: 12, color: "#ffcc66" }}>No profile yet — the onboarding wizard will appear, or build one from the Chat tab.</div>}
          {profile && (
            <div style={{ fontSize: 12, color: "#a8a8c0" }}>
              <div style={{ fontWeight: 700, color: "#d0d0e0" }}>{profile.persona_label}</div>
              <div>goals: {profile.answers?.goals || "—"}</div>
              <div>skills: {(profile.skills_created || []).join(", ") || "—"}</div>
            </div>
          )}
        </Section>

      </div>
    </div>
  );
}
