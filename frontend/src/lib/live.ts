import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import type { Notification } from "./types";

export type LiveState = "connecting" | "live" | "offline";

interface Options {
  token: string | null;
  onNotification: (n: Pick<Notification, "notification_id" | "lead_id" | "kind" | "title">) => void;
}

/**
 * Keeps one WebSocket to /ws/notifications open while signed in. New alerts and lead
 * changes refresh the matching queries; if the connection drops it retries with backoff,
 * and the pages also refetch on a timer, so nothing is missed while offline.
 */
export function useLiveUpdates({ token, onNotification }: Options): LiveState {
  const queryClient = useQueryClient();
  const [state, setState] = useState<LiveState>("connecting");
  const notify = useRef(onNotification);
  notify.current = onNotification;

  useEffect(() => {
    if (!token) return;
    let socket: WebSocket | null = null;
    let retry = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let ping: ReturnType<typeof setInterval> | undefined;
    let stopped = false;

    const connect = () => {
      setState("connecting");
      const proto = window.location.protocol === "https:" ? "wss" : "ws";
      socket = new WebSocket(`${proto}://${window.location.host}/ws/notifications?token=${encodeURIComponent(token)}`);
      socket.onopen = () => {
        retry = 0;
        setState("live");
        ping = setInterval(() => socket?.readyState === WebSocket.OPEN && socket.send("ping"), 25000);
      };
      socket.onmessage = (ev) => {
        if (ev.data === "pong") return;
        let msg: { type: string; notification?: Notification; lead_ids?: string[] };
        try {
          msg = JSON.parse(ev.data);
        } catch {
          return;
        }
        if (msg.type === "notification" && msg.notification) {
          queryClient.invalidateQueries({ queryKey: ["notifications"] });
          queryClient.invalidateQueries({ queryKey: ["leads"] });
          notify.current(msg.notification);
        } else if (msg.type === "leads_changed") {
          queryClient.invalidateQueries({ queryKey: ["leads"] });
          queryClient.invalidateQueries({ queryKey: ["counts"] });
          for (const id of msg.lead_ids ?? []) queryClient.invalidateQueries({ queryKey: ["lead", id] });
        }
      };
      socket.onclose = (ev) => {
        clearInterval(ping);
        if (stopped) return;
        setState("offline");
        if (ev.code === 4401) return; // token rejected: the next API call signs out
        retry = Math.min(retry + 1, 6);
        timer = setTimeout(connect, 1000 * 2 ** (retry - 1));
      };
    };
    connect();
    return () => {
      stopped = true;
      clearTimeout(timer);
      clearInterval(ping);
      socket?.close();
    };
  }, [token, queryClient]);

  return token ? state : "offline";
}
