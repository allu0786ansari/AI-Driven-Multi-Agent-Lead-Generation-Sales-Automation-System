import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { HeatBar, Score, StatusWord } from "../../components/heat";
import { useToast } from "../../components/toasts";
import { Button, Input } from "../../components/ui";
import { api } from "../../lib/api";
import { formatCost, rationaleLines, shortTime, slotFacts, SOURCE_LABEL, windowLeft } from "../../lib/format";
import type { LeadDetail, QualificationConfig } from "../../lib/types";

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-2 border-t border-rule px-5 py-4">
      <h3 className="text-sm font-semibold text-ink">{title}</h3>
      {children}
    </section>
  );
}

function Row({ label, value }: { label: string; value: ReactNode }) {
  if (value == null || value === "") return null;
  return (
    <div className="grid grid-cols-[6.5rem_1fr] gap-2 text-sm">
      <dt className="text-ink-3">{label}</dt>
      <dd className="min-w-0 break-words text-ink">{value}</dd>
    </div>
  );
}

export function Facts({ lead }: { lead: LeadDetail }) {
  const config = useQuery({ queryKey: ["settings", "qualification"], queryFn: () => api<QualificationConfig>("/api/settings/qualification"), staleTime: 60000 });
  const facts = slotFacts(lead.qualify_slots ?? {});
  const reasons = rationaleLines(lead.score_rationale);
  const source = lead.source ? SOURCE_LABEL[lead.source] ?? lead.source : null;

  return (
    <div className="flex flex-col">
      <div className="flex flex-col gap-3 px-5 pt-5 pb-4">
        <div className="flex items-end justify-between gap-3">
          <Score status={lead.status} score={lead.qualification_score} size="xl" />
          <StatusWord status={lead.status} score={lead.qualification_score} />
        </div>
        <HeatBar
          status={lead.status}
          score={lead.qualification_score}
          warmAt={config.data?.warm_threshold}
          hotAt={config.data?.hot_threshold}
        />
        {reasons.length > 0 ? (
          <ul className="flex flex-col gap-1 text-sm">
            {reasons.map((r, i) => (
              <li key={i} className="flex justify-between gap-3">
                <span className="text-ink-2">{r.text}</span>
                {r.points != null && (
                  <span className={`tabular shrink-0 font-medium ${r.points < 0 ? "text-danger" : "text-ink"}`}>
                    {r.points > 0 ? `+${r.points}` : r.points}
                  </span>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-ink-3">Not scored yet. The score grows as the customer shares what they need.</p>
        )}
      </div>

      <Section title="What they told us">
        {facts.length ? (
          <dl className="flex flex-col gap-1.5">
            {facts.map((f) => (
              <Row key={f.label} label={f.label} value={f.value} />
            ))}
          </dl>
        ) : (
          <p className="text-sm text-ink-3">Nothing yet.</p>
        )}
      </Section>

      <Section title="Contact">
        <dl className="flex flex-col gap-1.5">
          <Row label="Phone" value={lead.contact_phone} />
          <Row label="Email" value={lead.contact_email} />
          <Row label="Instagram" value={lead.instagram_scoped_id ? "Connected" : null} />
          <Row label="Messenger" value={lead.messenger_scoped_id ? "Connected" : null} />
          <Row
            label="WhatsApp offers"
            value={lead.opted_out ? "Opted out of all messages" : lead.whatsapp_opt_in ? "Agreed" : "Not agreed"}
          />
          <Row label="Language" value={lead.language} />
        </dl>
      </Section>

      <Section title="Where they came from">
        <dl className="flex flex-col gap-1.5">
          <Row label="Source" value={source} />
          <Row label="Details" value={lead.source_detail} />
          <Row label="Ad" value={lead.ad_id} />
          <Row label="First contact" value={shortTime(lead.created_at)} />
        </dl>
      </Section>

      <Section title="Messaging">
        <dl className="flex flex-col gap-1.5">
          <Row
            label="Free replies"
            value={lead.free_entry_window_expires_at ? `${windowLeft(lead.free_entry_window_expires_at)} (ad window)` : null}
          />
          <Row label="WhatsApp cost" value={formatCost(lead.messaging_cost_estimate)} />
        </dl>
      </Section>

      <Assign lead={lead} />
    </div>
  );
}

function Assign({ lead }: { lead: LeadDetail }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [value, setValue] = useState(lead.assigned_to ?? "");
  const save = useMutation({
    mutationFn: () => api(`/api/leads/${lead.lead_id}`, { method: "PATCH", json: { assigned_to: value } }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["lead", lead.lead_id] });
      toast.show(value.trim() ? `Assigned to ${value.trim()}` : "Assignment removed");
    },
    onError: (e: Error) => toast.show(e.message, { tone: "error" }),
  });
  const changed = value.trim() !== (lead.assigned_to ?? "");
  return (
    <Section title="Assigned to">
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (changed) save.mutate();
        }}
      >
        <Input aria-label="Assigned to" placeholder="Name of the salesperson" value={value} onChange={(e) => setValue(e.target.value)} className="h-9" />
        <Button type="submit" disabled={!changed} busy={save.isPending}>
          Save
        </Button>
      </form>
    </Section>
  );
}
