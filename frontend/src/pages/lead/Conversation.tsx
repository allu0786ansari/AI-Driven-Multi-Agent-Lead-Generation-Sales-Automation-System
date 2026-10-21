import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Check, CheckCheck, Clock } from "lucide-react";
import { useEffect, useRef } from "react";
import { ChannelMark } from "../../components/channel";
import { api } from "../../lib/api";
import { byTime, clockTime, dayLabel, fillTemplate, STATUS_LABEL } from "../../lib/format";
import type { Activity, LeadDetail, LeadStatus, Message, Template } from "../../lib/types";

type Item = { at: string; message?: Message; activity?: Activity };

const MEDIA: Record<string, string> = {
  audio: "Voice note",
  image: "Photo",
  video: "Video",
  document: "Document",
  sticker: "Sticker",
  location: "Location",
  contacts: "Contact card",
};

function whoLabel(m: Message): string | null {
  if (m.direction === "inbound") {
    return m.kind === "comment" ? "Commented on a post" : null;
  }
  const who = { agent: "Assistant", salesperson: "You", system: "Automatic reply", nurture: "Follow-up", lead: "" }[
    m.sender
  ];
  const by = { agent: "the assistant", salesperson: "you", system: "the system", nurture: "", lead: "" }[m.sender];
  if (m.sender === "nurture") return `Follow-up after a quiet spell, sent automatically`;
  if (m.kind === "private_reply") return `Private reply to their comment, by ${by}`;
  if (m.kind === "public_reply") return `Public reply under their comment, by ${by}`;
  if (m.template_name) return `Template "${m.template_name}", sent by ${by}`;
  return who;
}

function DeliveryState({ m }: { m: Message }) {
  const s = m.delivery_status;
  if (s === "failed")
    return (
      <span className="inline-flex items-center gap-1 text-danger" title={m.error ?? undefined}>
        <AlertTriangle className="size-3.5" aria-hidden /> Not delivered{m.error ? `: ${m.error}` : ""}
      </span>
    );
  if (s === "queued" || s === "sending")
    return (
      <span className="inline-flex items-center gap-1">
        <Clock className="size-3.5" aria-hidden /> Sending
      </span>
    );
  if (s === "read")
    return (
      <span className="inline-flex items-center gap-1" style={{ color: "var(--fb)" }}>
        <CheckCheck className="size-3.5" aria-hidden /> Read
      </span>
    );
  if (s === "delivered")
    return (
      <span className="inline-flex items-center gap-1">
        <CheckCheck className="size-3.5" aria-hidden /> Delivered
      </span>
    );
  return (
    <span className="inline-flex items-center gap-1">
      <Check className="size-3.5" aria-hidden /> Sent
    </span>
  );
}

function Bubble({ m, templates }: { m: Message; templates: Template[] }) {
  const inbound = m.direction === "inbound";
  const label = whoLabel(m);
  let body = m.text ?? (m.media_type !== "none" ? `[${MEDIA[m.media_type] ?? m.media_type}]` : "");
  if (m.template_name && body.startsWith("[template ")) {
    // Stored as "[template name] param1 | param2": show what the customer saw, if we know the template.
    const params = body.replace(/^\[template [^\]]*\]\s*/, "").split(" | ").filter(Boolean);
    const known = templates.find((t) => t.name === m.template_name)?.body;
    body = known
      ? fillTemplate(known, params)
      : params.length
        ? `Filled in with: ${params.join(", ")}`
        : "Template sent";
  }
  // Who wrote it is visible at a glance: customer grey on the left; the assistant outlined,
  // you filled, automatic replies dashed, on the right.
  const style = inbound
    ? "bg-sheet-2 text-ink rounded-2xl rounded-bl-md"
    : m.sender === "salesperson"
      ? "bg-ink text-sheet rounded-2xl rounded-br-md"
      : m.sender === "system" || m.sender === "nurture"
        ? "border border-dashed border-ink-3 text-ink rounded-2xl rounded-br-md"
        : "border border-ink-2 bg-sheet text-ink rounded-2xl rounded-br-md";
  return (
    <div className={`flex flex-col gap-1 ${inbound ? "items-start" : "items-end"}`}>
      {label && <span className="px-1 text-xs font-medium text-ink-3">{label}</span>}
      <div className={`max-w-[min(34rem,85%)] px-3.5 py-2.5 ${style} ${m.kind === "comment" ? "italic" : ""}`}>
        <p className="msg-text">{body}</p>
      </div>
      <span className="flex items-center gap-2 px-1 text-xs text-ink-3">
        <ChannelMark channel={m.channel} />
        <span className="tabular">{clockTime(m.timestamp)}</span>
        {!inbound && m.kind !== "public_reply" && <DeliveryState m={m} />}
      </span>
    </div>
  );
}

function activityText(a: Activity, me: string | undefined): string {
  const who = a.actor === me ? "You" : a.actor;
  switch (a.kind) {
    case "status":
      return `${who} marked this lead as ${STATUS_LABEL[(a.detail.new as LeadStatus) ?? "new"] ?? a.detail.new}`;
    case "assign":
      return a.detail.new ? `${who} assigned it to ${a.detail.new}` : `${who} removed the assignment`;
    case "takeover":
      return a.detail.reason === "replied manually"
        ? `${who} replied, so the assistant paused`
        : `${who} took over; the assistant paused`;
    case "release":
      return `${who} handed it back to the assistant`;
    case "consent":
      return a.detail.answer === "no" ? "The customer said no to offers and reminders" : "";
    case "erase":
      return `${who} erased the customer's personal data`;
    case "feedback":
      return a.detail.agree ? `${who} confirmed this lead is hot` : `${who} said this lead is not really hot`;
    default:
      return "";
  }
}

const SHOWN_AS_MESSAGE = new Set(["message", "follow_up"]);

export function Conversation({ lead, me }: { lead: LeadDetail; me?: string }) {
  const end = useRef<HTMLDivElement>(null);
  const templates = useQuery({
    queryKey: ["templates"],
    queryFn: () => api<{ templates: Template[] }>("/api/templates"),
    staleTime: 10 * 60_000,
    enabled: lead.messages.some((m) => m.template_name),
  });
  const items: Item[] = [
    ...lead.messages.map((m) => ({ at: m.timestamp, message: m })),
    ...lead.activity
      .filter((a) => !SHOWN_AS_MESSAGE.has(a.kind) && activityText(a, me))
      .map((a) => ({ at: a.created_at, activity: a })),
  ].sort(byTime);

  const count = lead.messages.length;
  useEffect(() => {
    end.current?.scrollIntoView({ block: "end" });
  }, [lead.lead_id, count]);

  let lastDay = "";
  return (
    <div className="flex flex-col gap-4 px-4 py-6 md:px-8">
      {items.length === 0 && <p className="text-center text-sm text-ink-3">No messages yet.</p>}
      {items.map((item) => {
        const day = dayLabel(item.at);
        const showDay = day !== lastDay;
        lastDay = day;
        return (
          <div key={item.message?.message_id ?? item.activity!.activity_id} className="flex flex-col gap-4">
            {showDay && <p className="text-center text-xs font-medium text-ink-3">{day}</p>}
            {item.message ? (
              <Bubble m={item.message} templates={templates.data?.templates ?? []} />
            ) : (
              <p className="text-center text-xs text-ink-2">
                {activityText(item.activity!, me)} <span className="tabular text-ink-3">{clockTime(item.at)}</span>
              </p>
            )}
          </div>
        );
      })}
      <div ref={end} />
    </div>
  );
}
