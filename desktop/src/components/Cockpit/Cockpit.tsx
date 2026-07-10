import React from "react";
import ChatView from "../Chat/ChatView";
import EditorPane from "../Editor/EditorPane";

/** Cockpit — the one-page IDE: chat (left) drives the agent while the editor
 *  (right) shows the code it touches. Changeset reviews render inline in chat. */
export default function Cockpit() {
  return (
    <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>
      <div style={{ width: "42%", minWidth: 380, display: "flex", borderRight: "1px solid #2a2a3e" }}>
        <ChatView />
      </div>
      <div style={{ flex: 1, display: "flex", minWidth: 0 }}>
        <EditorPane />
      </div>
    </div>
  );
}
