import React, { useEffect, useState } from "react";
import { useWs } from "../../hooks/useWebSocket";

/** First-launch onboarding — mirrors the CLI interview. Appears when the agent
 *  reports no profile; answers are sent as profile:build:<json>. */

const QUESTIONS: { key: string; label: string; placeholder: string; options?: string[] }[] = [
  {
    key: "role", label: "What best describes you?", placeholder: "e.g. backend developer",
    options: ["developer", "project_manager", "doctor_medical", "engineer", "data_scientist", "researcher", "designer", "writer", "student", "business"],
  },
  { key: "domains", label: "What domains will you work on most?", placeholder: "e.g. web apps, trading, research" },
  { key: "stack", label: "Main tools / languages?", placeholder: "e.g. python, react, excel, none" },
  { key: "experience", label: "Experience with AI assistants?", placeholder: "beginner / intermediate / expert", options: ["beginner", "intermediate", "expert"] },
  { key: "goals", label: "What should Daedalus help you achieve first?", placeholder: "e.g. ship my side project" },
];

export default function OnboardingWizard() {
  const { sendCommand, subscribe, connected } = useWs();
  const [profileState, setProfileState] = useState<"unknown" | "missing" | "present">("unknown");
  const [dismissed, setDismissed] = useState(false);
  const [step, setStep] = useState(0);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [current, setCurrent] = useState("");
  const [built, setBuilt] = useState<any>(null);

  useEffect(() => {
    const unsub = subscribe("profile", (m: any) => {
      if (m.data && m.data.persona) {
        setProfileState("present");
        if (built === null && step > 0) setBuilt(m.data); // response to our build
      } else if (m.data === null || m.data === undefined) {
        setProfileState((s) => (s === "present" ? s : "missing"));
      }
    });
    return unsub;
  }, [subscribe, built, step]);

  useEffect(() => {
    if (connected) sendCommand("profile");
  }, [connected, sendCommand]);

  if (dismissed || profileState !== "missing") return null;

  const question = QUESTIONS[step];

  const submitAnswer = (value: string) => {
    const next = { ...answers, [question.key]: value.trim() };
    setAnswers(next);
    setCurrent("");
    if (step + 1 < QUESTIONS.length) {
      setStep(step + 1);
    } else {
      sendCommand(`profile:build:${JSON.stringify(next)}`);
      setProfileState("present");
      setDismissed(true);
    }
  };

  return (
    <div style={{
      position: "fixed", inset: 0, zIndex: 900, display: "flex",
      alignItems: "center", justifyContent: "center",
      background: "rgba(16, 16, 30, 0.88)", backdropFilter: "blur(4px)",
    }}>
      <div style={{
        width: 480, background: "#1e1e34", border: "1px solid #2a2a3e",
        borderRadius: 16, padding: 32, display: "flex", flexDirection: "column", gap: 16,
      }}>
        <div>
          <div style={{ fontSize: 18, fontWeight: 700, color: "#e0e0e0" }}>Welcome to Daedalus</div>
          <div style={{ fontSize: 12, color: "#888", marginTop: 4 }}>
            A few questions so Daedalus can pre-build skills and adapt to you. ({step + 1}/{QUESTIONS.length})
          </div>
        </div>

        <div style={{ fontSize: 14, color: "#c8c8dc" }}>{question.label}</div>

        {question.options && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {question.options.map((opt) => (
              <button key={opt} onClick={() => submitAnswer(opt)} style={{
                padding: "6px 12px", borderRadius: 16, fontSize: 12, cursor: "pointer",
                border: "1px solid #3a3a5c", background: "#252540", color: "#b8b8d0",
              }}>{opt.replace("_", " ")}</button>
            ))}
          </div>
        )}

        <div style={{ display: "flex", gap: 8 }}>
          <input
            autoFocus
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submitAnswer(current)}
            placeholder={question.placeholder}
            style={{
              flex: 1, padding: "10px 12px", borderRadius: 8, fontSize: 13,
              border: "1px solid #3a3a5c", background: "#16162a", color: "#e0e0e0", outline: "none",
            }}
          />
          <button onClick={() => submitAnswer(current)} style={{
            padding: "10px 18px", borderRadius: 8, border: "none", cursor: "pointer",
            background: "#7c7cff", color: "#fff", fontWeight: 700, fontSize: 13,
          }}>{step + 1 === QUESTIONS.length ? "Finish" : "Next"}</button>
        </div>

        <button onClick={() => setDismissed(true)} style={{
          alignSelf: "center", background: "none", border: "none",
          color: "#666", fontSize: 12, cursor: "pointer",
        }}>Skip setup — I'll do it later</button>
      </div>
    </div>
  );
}
