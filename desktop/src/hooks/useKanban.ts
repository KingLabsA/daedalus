import { useCallback, useEffect, useRef } from "react";
import { useStore } from "../store/session";
import { useWs } from "./useWebSocket";

export function useKanban() {
  const kanban = useStore((s) => s.kanban);
  const setKanban = useStore((s) => s.setKanban);
  const { sendCommand, subscribe, connected } = useWs();
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    const unsub = subscribe("kanban", (data: any) => {
      if (data.data) setKanban(data.data);
    });
    return unsub;
  }, [subscribe, setKanban]);

  useEffect(() => {
    if (!connected) return;
    sendCommand("kanban");
    pollRef.current = setInterval(() => {
      sendCommand("kanban");
    }, 5000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [connected, sendCommand]);

  const addTask = useCallback(
    (title: string) => {
      sendCommand(`kanban:add:${title}`);
    },
    [sendCommand]
  );

  const moveTask = useCallback(
    (taskId: string, toStatus: string) => {
      sendCommand(`kanban:move:${taskId}:${toStatus}`);
    },
    [sendCommand]
  );

  const removeTask = useCallback(
    (taskId: string) => {
      sendCommand(`kanban:remove:${taskId}`);
    },
    [sendCommand]
  );

  return { kanban, addTask, moveTask, removeTask };
}
