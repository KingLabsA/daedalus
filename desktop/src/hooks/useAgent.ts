import { useState, useCallback, useEffect } from "react";
import { useWs } from "./useWebSocket";

export function useAgent() {
  const [loading, setLoading] = useState(false);
  const { send: wsSend, connected, connecting, subscribe, command } = useWs();

  // loading stays true for the WHOLE run — cleared only when the agent's final
  // response (or a cancel/error notification) arrives, so the Stop button is
  // visible the entire time the agent is working.
  useEffect(() => {
    const subs = [
      subscribe("response", () => setLoading(false)),
      subscribe("notification", () => setLoading(false)),
    ];
    return () => subs.forEach((u) => u());
  }, [subscribe]);

  const send = useCallback((text: string) => {
    setLoading(true);
    wsSend(text);
  }, [wsSend]);

  const cancel = useCallback(() => {
    command("cancel");
    setLoading(false);
  }, [command]);

  return { send, cancel, loading, connected, connecting };
}
