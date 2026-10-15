import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Bot, Hand, PanelRight } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ChannelMark } from "../../components/channel";
import { useToast } from "../../components/toasts";
import { Button, ErrorNote, Select, Spinner } from "../../components/ui";
import { api } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { displayName, STATUS_LABEL } from "../../lib/format";
import type { LeadDetail, LeadStatus } from "../../lib/types";
import { Composer } from "./Composer";
import { Conversation } from "./Conversation";
import { Facts } from "./Facts";

const MARK_AS: LeadStatus[] = ["contacted", "converted", "lost"];

export function LeadPane() {
  const { leadId = "" } = useParams();
  const { me } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const [showFacts, setShowFacts] = useState(false);

  const lead = useQuery({
    queryKey: ["lead", leadId],
    queryFn: () => api<LeadDetail>(`/api/leads/${leadId}`),
    refetchInterval: 20000,
  });

  // Opening a lead marks its alerts as read.
  const unread = lead.data?.unread_alerts ?? 0;
  useEffect(() => {
    if (!unread) return;
    api("/api/notifications/read", { method: "POST", json: { lead_id: leadId } }).then(() => {
      qc.invalidateQueries({ queryKey: ["notifications"] });
      qc.invalidateQueries({ queryKey: ["leads"] });
    });
  }, [leadId, unread, qc]);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["lead", leadId] });
    qc.invalidateQueries({ queryKey: ["leads"] });
    qc.invalidateQueries({ queryKey: ["counts"] });
  };
  const takeover = useMutation({
    mutationFn: (on: boolean) => api(`/api/leads/${leadId}/${on ? "takeover" : "release"}`, { method: "POST" }),
    onSuccess: (_d, on) => {
      refresh();
      toast.show(on ? "You're handling this conversation. The assistant is paused." : "Handed back. The assistant answers their next message.");
    },
    onError: (e: Error) => toast.show(e.message, { tone: "error" }),
  });
  const setStatus = useMutation({
    mutationFn: (status: LeadStatus) => api(`/api/leads/${leadId}`, { method: "PATCH", json: { status } }),
    onSuccess: (_d, status) => {
      refresh();
      toast.show(`Marked as ${STATUS_LABEL[status]}`);
    },
    onError: (e: Error) => toast.show(e.message, { tone: "error" }),
  });

  if (lead.isPending) return <Spinner label="Loading conversation" />;
  if (lead.isError)
    return (
      <div className="flex w-full flex-col gap-4 p-6">
        <Link to="/leads" className="text-sm text-ink-2 hover:text-ink">
          Back to leads
        </Link>
        <ErrorNote error={lead.error} />
      </div>
    );
  const l = lead.data;
  const closed = l.status === "converted" || l.status === "lost";

  return (
    <div className="flex h-full w-full min-w-0">
      <section className="flex min-w-0 flex-1 flex-col bg-steel" aria-label="Conversation">
        <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-rule bg-sheet px-4 py-3 md:px-8">
          <Link to="/leads" className="text-ink-2 hover:text-ink md:hidden" aria-label="Back to leads">
            <ArrowLeft className="size-5" />
          </Link>
          <div className="min-w-[12rem] flex-1">
            <h2 className="wide truncate text-xl font-extrabold">{displayName(l)}</h2>
            <div className="flex flex-wrap items-center gap-x-3 text-sm text-ink-2">
              <ChannelMark channel={l.default_channel ?? l.preferred_channel} label />
              {l.contact_phone && l.name && <span className="tabular">{l.contact_phone}</span>}
              {l.product_interest && <span className="truncate">{l.product_interest}</span>}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {!closed &&
              (l.is_human_takeover ? (
                <Button onClick={() => takeover.mutate(false)} busy={takeover.isPending} icon={<Bot className="size-4" aria-hidden />}>
                  Hand back to assistant
                </Button>
              ) : (
                <Button tone="primary" onClick={() => takeover.mutate(true)} busy={takeover.isPending} icon={<Hand className="size-4" aria-hidden />}>
                  Take over
                </Button>
              ))}
            <Select
              aria-label="Mark lead as"
              value=""
              onChange={(e) => e.target.value && setStatus.mutate(e.target.value as LeadStatus)}
              className="h-9 w-auto text-sm"
              disabled={setStatus.isPending}
            >
              <option value="">Mark as…</option>
              {MARK_AS.filter((s) => s !== l.status).map((s) => (
                <option key={s} value={s}>
                  {STATUS_LABEL[s]}
                </option>
              ))}
              {closed && <option value="warm">Open again</option>}
            </Select>
            <Button tone="ghost" onClick={() => setShowFacts((v) => !v)} className="xl:hidden" aria-expanded={showFacts} icon={<PanelRight className="size-4" aria-hidden />}>
              Details
            </Button>
          </div>
        </header>

        {l.is_human_takeover && !closed && (
          <p className="border-b border-rule bg-sheet-2 px-4 py-2 text-sm text-ink md:px-8">
            {l.assigned_to
              ? `${l.assigned_to === me?.email ? "You're" : `${l.assigned_to} is`} handling this conversation. The assistant won't reply until it's handed back.`
              : "The assistant handed this conversation to a person. Reply to the customer, or hand it back to the assistant."}
          </p>
        )}

        <div className="min-h-0 flex-1 overflow-y-auto">
          <Conversation lead={l} me={me?.email} />
        </div>
        <Composer key={l.lead_id} lead={l} />
      </section>

      <aside
        className={`${showFacts ? "fixed inset-0 z-30 block md:static md:inset-auto" : "hidden"} w-full overflow-y-auto border-rule md:border-l bg-sheet md:w-80 md:shrink-0 xl:block`}
        aria-label="Lead details"
      >
        <div className="flex items-center justify-between px-5 pt-4 xl:hidden">
          <span className="text-sm font-semibold">Details</span>
          <Button tone="ghost" onClick={() => setShowFacts(false)}>
            Close
          </Button>
        </div>
        <Facts key={l.lead_id} lead={l} />
      </aside>
    </div>
  );
}
