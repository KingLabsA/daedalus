import React, { createContext, useContext, useEffect, useRef, useCallback, useState } from "react";
import { useStore } from "../store/session";
import { Message, KanbanState } from "../types";

const WS_URL = "ws://127.0.0.1:8765";

export interface WsContextValue {
  send: (text: string) => void;
  sendCommand: (cmd: string) => void;
  switchProvider: (p: string) => void;
  command: (cmd: string) => void;
  connected: boolean;
  connecting: boolean;
  subscribe: (type: string, handler: (data: any) => void) => () => void;
}

const WsContext = createContext<WsContextValue | null>(null);

export function WsProvider({ children }: { children: React.ReactNode }) {
  const wsRef = useRef<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const handlersRef = useRef<Map<string, Set<(data: any) => void>>>(new Map());
  const store = useStore;
  const reconnectRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    setConnecting(true);
    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;

      ws.onopen = () => {
        setConnected(true);
        setConnecting(false);
        store.getState().setConnected(true);
        store.getState().setConnecting(false);
        // fetch initial state
        ws.send(JSON.stringify({ type: "command", command: "tools" }));
        ws.send(JSON.stringify({ type: "command", command: "skills" }));
        ws.send(JSON.stringify({ type: "command", command: "kanban" }));
        ws.send(JSON.stringify({ type: "command", command: "diff" }));
        ws.send(JSON.stringify({ type: "command", command: "lsp" }));
        ws.send(JSON.stringify({ type: "command", command: "logs" }));
        ws.send(JSON.stringify({ type: "command", command: "sessions" }));
      };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        const st = store.getState();

        switch (data.type) {
          case "kanban":
            st.setKanban(data.data);
            break;
          case "tools":
            st.setTools(data.data);
            break;
          case "skills":
            st.setSkills(data.data);
            break;
          case "provider":
            st.setProvider(data.data);
            break;
          case "logs":
            st.setLogs(data.data || []);
            break;
          case "diff":
            st.setDiff(data.data || "");
            break;
          case "lsp":
            st.setLsp(data.data || "");
            break;
          case "sessions":
            st.setSessions(data.data || []);
            break;
          case "notification":
            st.addMessage({
              id: `notif-${Date.now()}`,
              role: "assistant",
              content: data.content || JSON.stringify(data.data),
              timestamp: new Date(),
            });
            break;
          case "stream":
            st.appendStream(data.data || []);
            break;
          case "cost":
            st.setCost(data.data);
            break;
          case "provider_test_result":
            st.setProviderTestResult(data.data);
            break;
          case "token":
            if (st.streamingMessageId) {
              st.setStreamingContent(st.streamingContent + data.content);
            } else {
              const newId = `stream-${Date.now()}`;
              st.setStreamingMessageId(newId);
              st.setStreamingContent(data.content);
            }
            break;
          case "response":
            if (st.streamingMessageId) {
              st.addMessage({
                id: st.streamingMessageId,
                role: "assistant",
                content: data.content,
                timestamp: new Date(),
              });
              st.setStreamingContent("");
              st.setStreamingMessageId(null);
            }
            break;
          case "files":
            st.setFiles(data.data);
            break;
          case "git_branches":
            st.setGitBranches(data.data);
            break;
          case "git_log":
            st.setGitLog(data.data);
            break;
        }

        // notify subscribers
        const subs = handlersRef.current.get(data.type);
        if (subs) {
          subs.forEach((fn) => fn(data));
        }
      } catch {
        // ignore non-JSON messages
      }
    };

    ws.onclose = () => {
      setConnected(false);
      setConnecting(false);
      store.getState().setConnected(false);
      store.getState().setConnecting(false);
      wsRef.current = null;
    };

    ws.onerror = () => {
      ws.close();
    };
  }, []);

  useEffect(() => {
    connect();

    reconnectRef.current = setInterval(() => {
      if (!wsRef.current || wsRef.current.readyState === WebSocket.CLOSED) {
        connect();
      }
    }, 3000);

    return () => {
      if (reconnectRef.current) clearInterval(reconnectRef.current);
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
      setConnected(false);
      setConnecting(false);
    };
  }, [connect]);

  const send = useCallback((text: string) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      store.getState().addMessage({
        id: `m-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
        role: "assistant",
        content: "⚠️ Not connected to agent. Start the agent first.",
        timestamp: new Date(),
      });
      return;
    }
    const userMsg: Message = {
      id: `m-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
      role: "user",
      content: text,
      timestamp: new Date(),
    };
    store.getState().addMessage(userMsg);
    ws.send(JSON.stringify({ type: "chat", text }));
  }, []);

  const sendCommand = useCallback((cmd: string) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({ type: "command", command: cmd }));
  }, []);

  const switchProvider = useCallback((p: string) => {
    sendCommand(`provider:${p}`);
  }, [sendCommand]);

  const command = sendCommand;

  const subscribe = useCallback((type: string, handler: (data: any) => void) => {
    if (!handlersRef.current.has(type)) {
      handlersRef.current.set(type, new Set());
    }
    handlersRef.current.get(type)!.add(handler);
    return () => {
      handlersRef.current.get(type)?.delete(handler);
    };
  }, []);

  return (
    <WsContext.Provider value={{ send, sendCommand, switchProvider, command, connected, connecting, subscribe }}>
      {children}
    </WsContext.Provider>
  );
}

export function useWs(): WsContextValue {
  const ctx = useContext(WsContext);
  if (!ctx) throw new Error("useWs must be used within WsProvider");
  return ctx;
}
