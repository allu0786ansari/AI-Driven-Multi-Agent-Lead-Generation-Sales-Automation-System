import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { useDeferredValue, useState } from "react";
import { Link, Outlet, useParams, useSearchParams } from "react-router-dom";
import { ChannelMark } from "../components/channel";
import { Score } from "../components/heat";
import { Button, Empty, ErrorNote, Input, Select, Spinner } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { displayName, handlerText, HEAT_VAR, heatOf, previewText, shortTime } from "../lib/format";
import type { Counts, LeadSummary, View } from "../lib/types";

const TABS: { view: View; label: string }[] = [
  { view: "needs_you", label: "Needs you" },
  { view: "open", label: "Open" },
  { view: "all", label: "All" },
  { view: "closed", label: "Closed" },
];

const PAGE = 50;

const EMPTY: Record<View, { title: string; body: string }> = {
  needs_you: {
    title: "Nobody is waiting for you",
    body: "Hot leads and conversations the assistant hands to you appear here. The assistant is answering everyone else.",
  },
  open: { title: "No open leads yet", body: "Leads appear here as soon as someone messages you, comments, or fills in a lead form." },
  all: { title: "No leads yet", body: "Leads appear here as soon as someone messages you, comments, or fills in a lead form." },
  closed: { title: "No closed leads", body: "Leads you mark as Bought or Lost move here." },
};

function preview(lead: LeadSummary): string {
  const text = previewText(lead.last_message_text ?? lead.conversation_summary ?? "");
  if (lead.last_message_direction !== "outbound") return text;
  const who =
    lead.last_message_sender === "salesperson" ? "You" : lead.last_message_sender === "nurture" ? "Follow-up" : "Assistant";
  return `${who}: ${text}`;
}

function Row({ lead, active, me }: { lead: LeadSummary; active: boolean; me?: string }) {
  const heat = heatOf(lead.status, lead.qualification_score);
  const handler = handlerText(lead, me);
  const closed = lead.status === "converted" || lead.status === "lost";
  return (
    <li>
      <Link
        to={`/leads/${lead.lead_id}`}
        aria-current={active ? "page" : undefined}
        className={`relative flex gap-3 border-b border-rule py-3 pr-4 pl-5 ${active ? "bg-sheet-2" : "hover:bg-sheet-2"}`}
      >
        <span
          className="absolute inset-y-0 left-0 w-1"
          style={{ background: closed ? "var(--rule)" : HEAT_VAR[heat] }}
          aria-hidden
        />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className={`truncate font-semibold ${closed ? "text-ink-2" : "text-ink"}`}>{displayName(lead)}</span>
            <ChannelMark channel={lead.last_message_channel ?? lead.preferred_channel} />
            {lead.unread_alerts > 0 && <span className="size-2 shrink-0 rounded-full bg-hot" title="New alert" />}
          </div>
          <p className="mt-0.5 line-clamp-2 text-sm text-ink-2">{preview(lead)}</p>
          {handler && !closed && <p className="mt-1 text-xs font-medium text-ink">{handler}</p>}
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1.5">
          <span className="text-xs text-ink-3 tabular">{shortTime(lead.last_message_at ?? lead.last_activity_at)}</span>
          {closed ? (
            <span className="text-xs font-medium text-ink-3">{lead.status === "converted" ? "Bought" : "Lost"}</span>
          ) : (
            <Score status={lead.status} score={lead.qualification_score} />
          )}
        </div>
      </Link>
    </li>
  );
}

export function Inbox() {
  const { leadId } = useParams();
  const { me } = useAuth();
  const [params, setParams] = useSearchParams();
  const view = (params.get("view") as View) || "needs_you";
  const channel = params.get("channel") || "";
  const [search, setSearch] = useState("");
  const q = useDeferredValue(search.trim());
  const [pages, setPages] = useState(1);

  const counts = useQuery({ queryKey: ["counts"], queryFn: () => api<Counts>("/api/leads/counts"), refetchInterval: 30000 });
  const leads = useQuery({
    queryKey: ["leads", view, channel, q, pages],
    queryFn: () => {
      const sp = new URLSearchParams({ view, limit: String(PAGE * pages) });
      if (channel) sp.set("channel", channel);
      if (q) sp.set("q", q);
      return api<LeadSummary[]>(`/api/leads?${sp}`);
    },
    placeholderData: keepPreviousData,
    refetchInterval: 30000,
  });

  const setParam = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
    setPages(1);
  };

  return (
    <div className="flex h-full">
      <section
        className={`flex h-full w-full flex-col border-r border-rule bg-sheet md:w-[22rem] md:shrink-0 lg:w-[24rem] ${leadId ? "hidden md:flex" : "flex"}`}
        aria-label="Leads"
      >
        <div className="flex flex-col gap-3 px-5 pt-5 pb-3">
          <h1 className="wide text-2xl font-extrabold">Leads</h1>
          <div className="relative">
            <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-ink-3" aria-hidden />
            <Input
              type="search"
              placeholder="Search name, phone or product"
              aria-label="Search leads"
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setPages(1);
              }}
              className="pl-9"
            />
          </div>
          <Select aria-label="Channel" value={channel} onChange={(e) => setParam("channel", e.target.value)} className="h-9 text-sm">
            <option value="">All channels</option>
            <option value="whatsapp">WhatsApp</option>
            <option value="instagram">Instagram</option>
            <option value="messenger">Messenger</option>
          </Select>
        </div>
        <div role="tablist" aria-label="Show" className="flex gap-1 border-b border-rule px-3">
          {TABS.map((t) => {
            const selected = t.view === view;
            const n = counts.data?.[t.view];
            return (
              <button
                key={t.view}
                role="tab"
                aria-selected={selected}
                type="button"
                onClick={() => setParam("view", t.view === "needs_you" ? "" : t.view)}
                className={`-mb-px flex items-center gap-1.5 border-b-2 px-2 pt-1 pb-2.5 text-sm font-medium ${selected ? "border-ink text-ink" : "border-transparent text-ink-3 hover:text-ink"}`}
              >
                {t.label}
                {n != null && (
                  <span
                    className="tabular text-xs"
                    style={{ color: t.view === "needs_you" && n > 0 ? "var(--hot)" : undefined }}
                  >
                    {n}
                  </span>
                )}
              </button>
            );
          })}
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {leads.isPending ? (
            <Spinner label="Loading leads" />
          ) : leads.isError ? (
            <div className="p-4">
              <ErrorNote error={leads.error} />
            </div>
          ) : leads.data.length === 0 ? (
            q ? (
              <Empty title={`No leads match "${q}"`}>Try a phone number, a name or a product.</Empty>
            ) : (
              <Empty title={EMPTY[view].title}>{EMPTY[view].body}</Empty>
            )
          ) : (
            <>
              <ul>
                {leads.data.map((l) => (
                  <Row key={l.lead_id} lead={l} active={l.lead_id === leadId} me={me?.email} />
                ))}
              </ul>
              {leads.data.length >= PAGE * pages && (
                <div className="p-4">
                  <Button onClick={() => setPages((n) => n + 1)} busy={leads.isFetching} className="w-full">
                    Show more
                  </Button>
                </div>
              )}
            </>
          )}
        </div>
      </section>
      <div className={`h-full min-w-0 flex-1 ${leadId ? "flex" : "hidden md:flex"}`}>
        <Outlet />
      </div>
    </div>
  );
}

export function NoLeadSelected() {
  return (
    <div className="flex h-full w-full items-center justify-center p-8 text-center text-ink-3">
      <div>
        <div className="mx-auto mb-4 flex w-fit items-end gap-1" aria-hidden>
          <span className="h-5 w-2 rounded-sm bg-cold opacity-60" />
          <span className="h-7 w-2 rounded-sm bg-warm opacity-60" />
          <span className="h-9 w-2 rounded-sm bg-hot opacity-60" />
        </div>
        <p>Pick a lead to read the conversation.</p>
      </div>
    </div>
  );
}
