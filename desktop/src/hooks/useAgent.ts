import { useState, useCallback } from "react";
import { useWs } from "./useWebSocket";

export function useAgent() {
  const [loading, setLoading] = useState(false);
  const { send: wsSend, connected, connecting } = useWs();

  const send = useCallback((text: string) => {
    setLoading(true);
    wsSend(text);
    // loading resets when WS response arrives; for UX we clear after a timeout
    setTimeout(() => setLoading(false), 500);
  }, [wsSend]);

  return { send, loading, connected, connecting };
}
