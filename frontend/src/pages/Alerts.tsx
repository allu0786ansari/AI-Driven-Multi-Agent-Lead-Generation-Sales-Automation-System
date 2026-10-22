import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Button, Empty, ErrorNote, PageHeader, Spinner } from "../components/ui";
import { api } from "../lib/api";
import { shortTime } from "../lib/format";
import type { Notification } from "../lib/types";

const KIND: Record<Notification["kind"], { label: string; color: string }> = {
  hot_lead: { label: "Hot lead", color: "var(--hot)" },
  handoff: { label: "Needs a person", color: "var(--warm)" },
  customer_replied: { label: "Customer replied", color: "var(--ink-2)" },
  message_failed: { label: "Not delivered", color: "var(--danger)" },
};

const DELIVERY: Record<string, string> = {
  logged: "Shown here only (email and Telegram are off)",
  sent: "Also sent by",
  queued: "Sending",
  failed: "Could not be sent",
};

export function Alerts() {
  const qc = useQueryClient();
  const list = useQuery({
    queryKey: ["notifications", "list"],
    queryFn: () => api<Notification[]>("/api/notifications?limit=100"),
    refetchInterval: 30000,
  });
  const readAll = useMutation({
    mutationFn: () => api("/api/notifications/read", { method: "POST", json: { all: true } }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["notifications"] });
      qc.invalidateQueries({ queryKey: ["leads"] });
    },
  });
  const unread = list.data?.filter((n) => !n.read_at).length ?? 0;

  return (
    <div className="h-full overflow-y-auto bg-sheet">
      <PageHeader title="Alerts">
        <Button onClick={() => readAll.mutate()} disabled={!unread} busy={readAll.isPending}>
          Mark all as read
        </Button>
      </PageHeader>
      <p className="max-w-prose px-5 pt-4 text-sm text-ink-2 md:px-8">
        You get an alert when a lead turns hot, when the assistant hands a conversation to you, and when a customer writes while
        you're handling them.
      </p>
      {list.isPending ? (
        <Spinner />
      ) : list.isError ? (
        <div className="p-6">
          <ErrorNote error={list.error} />
        </div>
      ) : list.data.length === 0 ? (
        <Empty title="No alerts yet">They appear here and, once set up, on Telegram or by email.</Empty>
      ) : (
        <ul className="mt-4 max-w-3xl px-2 md:px-5">
          {list.data.map((n) => {
            const k = KIND[n.kind];
            const body = n.body.split("\n").filter((line) => line && !line.startsWith("Open: "));
            return (
              <li key={n.notification_id} className="border-b border-rule">
                <Link to={n.lead_id ? `/leads/${n.lead_id}` : "/leads"} className="flex gap-4 px-3 py-4 hover:bg-sheet-2">
                  <span className="mt-1.5 size-2.5 shrink-0 rounded-full" style={{ background: n.read_at ? "transparent" : k.color, border: `2px solid ${k.color}` }} />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-baseline justify-between gap-x-3">
                      <p className={`font-semibold ${n.read_at ? "text-ink-2" : "text-ink"}`}>{n.title.replace(/^\S+\s/, "")}</p>
                      <span className="text-xs text-ink-3 tabular">{shortTime(n.created_at)}</span>
                    </div>
                    <p className="mt-0.5 text-xs font-medium" style={{ color: k.color }}>
                      {k.label}
                    </p>
                    <div className="mt-2 flex flex-col gap-0.5 text-sm text-ink-2">
                      {body.slice(0, 4).map((line, i) => (
                        <p key={i} className="truncate">
                          {line}
                        </p>
                      ))}
                    </div>
                    <p className="mt-2 text-xs text-ink-3">
                      {DELIVERY[n.status] ?? n.status}
                      {n.status === "sent" && n.channels_sent.length ? ` ${n.channels_sent.join(" and ")}` : ""}
                      {n.error ? `: ${n.error}` : ""}
                    </p>
                  </div>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
