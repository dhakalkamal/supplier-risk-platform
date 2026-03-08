import { useState, useEffect, useRef, useCallback } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useAuth0 } from "@auth0/auth0-react";
import type { AlertFiredEvent, ScoreUpdatedEvent, WsEvent } from "@/types/api";

const RECONNECT_DELAYS = [1000, 2000, 4000, 30000];

export function useWebSocket(): {
  lastAlertEvent: AlertFiredEvent | null;
  lastScoreEvent: ScoreUpdatedEvent | null;
  isConnected: boolean;
} {
  const [lastAlertEvent, setLastAlertEvent] = useState<AlertFiredEvent | null>(null);
  const [lastScoreEvent, setLastScoreEvent] = useState<ScoreUpdatedEvent | null>(null);
  const [isConnected, setIsConnected] = useState(false);

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const attemptRef = useRef(0);
  const shouldReconnectRef = useRef(true);
  const connectRef = useRef<(() => Promise<void>) | undefined>(undefined);

  const queryClient = useQueryClient();
  const { getAccessTokenSilently, isAuthenticated } = useAuth0();

  const connect = useCallback(async () => {
    if (!isAuthenticated) return;

    try {
      const token = await getAccessTokenSilently();
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const url = `${protocol}//${window.location.host}/ws/alerts?token=${token}`;

      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        setIsConnected(true);
        attemptRef.current = 0;
        // Refresh alerts list on reconnect to catch any missed events
        void queryClient.invalidateQueries({ queryKey: ["alerts"] });
      };

      ws.onmessage = (event: MessageEvent<string>) => {
        try {
          const msg = JSON.parse(event.data) as WsEvent;
          if (msg.type === "alert.fired") {
            setLastAlertEvent(msg);
          } else if (msg.type === "score.updated") {
            setLastScoreEvent(msg);
          } else if (msg.type === "ping") {
            ws.send(JSON.stringify({ type: "pong" }));
          } else if (msg.type === "auth.expired") {
            shouldReconnectRef.current = false;
            ws.close();
          }
        } catch {
          // ignore malformed messages
        }
      };

      ws.onclose = () => {
        setIsConnected(false);
        wsRef.current = null;
        if (shouldReconnectRef.current) {
          const delay = RECONNECT_DELAYS[Math.min(attemptRef.current, RECONNECT_DELAYS.length - 1)];
          attemptRef.current++;
          reconnectTimerRef.current = setTimeout(() => {
            void connectRef.current?.();
          }, delay);
        }
      };

      ws.onerror = () => {
        ws.close();
      };
    } catch {
      if (shouldReconnectRef.current) {
        const delay = RECONNECT_DELAYS[Math.min(attemptRef.current, RECONNECT_DELAYS.length - 1)];
        attemptRef.current++;
        reconnectTimerRef.current = setTimeout(() => {
          void connectRef.current?.();
        }, delay);
      }
    }
  }, [isAuthenticated, getAccessTokenSilently, queryClient]);

  useEffect(() => {
    connectRef.current = connect;
  }, [connect]);

  useEffect(() => {
    if (!isAuthenticated) return;
    shouldReconnectRef.current = true;
    void connect();

    return () => {
      shouldReconnectRef.current = false;
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      wsRef.current?.close();
    };
  }, [isAuthenticated, connect]);

  return { lastAlertEvent, lastScoreEvent, isConnected };
}
