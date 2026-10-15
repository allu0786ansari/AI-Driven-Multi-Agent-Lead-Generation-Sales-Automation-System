import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { ErrorNote, PageHeader, Select, Spinner } from "../components/ui";
import { api } from "../lib/api";
import { shortTime } from "../lib/format";
import type { InboundEvent } from "../lib/types";

const TYPE_LABEL: Record<string, string> = {
  "whatsapp.message": "WhatsApp message",
  "whatsapp.status": "WhatsApp delivery update",
  "page.leadgen": "Lead form",
  "page.comment": "Facebook comment",
  "page.message": "Messenger message",
  "page.message_echo": "Messenger (our own message)",
  "instagram.comment": "Instagram comment",
  "instagram.message": "Instagram message",
  "instagram.message_echo": "Instagram (our own message)",
  unknown: "Unrecognised",
};

const STATUS: Record<string, { label: string; color: string }> = {
  processed: { label: "Handled", color: "var(--ok)" },
  ignored: { label: "Nothing to do", color: "var(--ink-3)" },
  pending: { label: "Waiting", color: "var(--warm)" },
  processing: { label: "Working", color: "var(--warm)" },
  retry: { label: "Will retry", color: "var(--warm)" },
  failed: { label: "Failed", color: "var(--danger)" },
};

/** Everything Meta sent us and what the system did with it - for checking the setup. */
export function Activity() {
  const [status, setStatus] = useState("");
  const events = useQuery({
    queryKey: ["events", status],
    queryFn: () =>
      api<{ counts_by_status: Record<string, number>; events: InboundEvent[] }>(`/api/events?limit=100${status ? `&status=${status}` : ""}`),
    refetchInterval: 10000,
  });

  return (
    <div className="h-full overflow-y-auto bg-sheet">
      <PageHeader title="Activity">
        <Select aria-label="Show" value={status} onChange={(e) => setStatus(e.target.value)} className="h-9 w-auto text-sm">
          <option value="">Everything</option>
          {Object.entries(STATUS).map(([k, v]) => (
            <option key={k} value={k}>
              {v.label} {events.data?.counts_by_status[k] != null ? `(${events.data.counts_by_status[k]})` : ""}
            </option>
          ))}
        </Select>
      </PageHeader>
      <p className="max-w-prose px-5 pt-4 text-sm text-ink-2 md:px-8">
        Every message, comment and form that arrived from Meta, and what the system did with it. Use it to check your Meta
        connection or to see why the assistant replied the way it did.
      </p>
      {events.isPending ? (
        <Spinner />
      ) : events.isError ? (
        <div className="p-6">
          <ErrorNote error={events.error} />
        </div>
      ) : events.data.events.length === 0 ? (
        <p className="px-8 py-10 text-ink-2">Nothing has arrived yet.</p>
      ) : (
        <div className="overflow-x-auto px-2 py-4 md:px-5">
          <table className="w-full min-w-[44rem] text-left text-sm">
            <thead className="text-ink-3">
              <tr className="border-b border-rule">
                <th className="px-3 py-2 font-medium">When</th>
                <th className="px-3 py-2 font-medium">What arrived</th>
                <th className="px-3 py-2 font-medium">Result</th>
                <th className="px-3 py-2 font-medium">What happened</th>
              </tr>
            </thead>
            <tbody>
              {events.data.events.map((e) => {
                const s = STATUS[e.status] ?? { label: e.status, color: "var(--ink-3)" };
                return (
                  <tr key={e.id} className="border-b border-rule align-top">
                    <td className="tabular px-3 py-2 whitespace-nowrap text-ink-3">{shortTime(e.received_at)}</td>
                    <td className="px-3 py-2">{TYPE_LABEL[e.event_type] ?? e.event_type}</td>
                    <td className="px-3 py-2 whitespace-nowrap">
                      <span className="inline-flex items-center gap-1.5">
                        <span className="size-2 rounded-full" style={{ background: s.color }} />
                        {s.label}
                        {e.attempts > 1 && <span className="text-ink-3"> ({e.attempts} tries)</span>}
                      </span>
                    </td>
                    <td className="max-w-md px-3 py-2 break-words text-ink-2">{e.error ?? e.result_note ?? ""}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
