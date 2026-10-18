import type { Channel, LeadStatus, QualifySlots } from "./types";

export type Heat = "hot" | "warm" | "cold" | "none";

/** Temperature from status (salesperson-set statuses fall back to the score). */
export function heatOf(status: LeadStatus, score: number | null): Heat {
  if (status === "hot" || status === "warm" || status === "cold") return status;
  if (score == null) return "none";
  if (score >= 70) return "hot";
  if (score >= 40) return "warm";
  return "cold";
}

export const HEAT_VAR: Record<Heat, string> = {
  hot: "var(--hot)",
  warm: "var(--warm)",
  cold: "var(--cold)",
  none: "var(--rule)",
};

export const STATUS_LABEL: Record<LeadStatus, string> = {
  new: "New",
  hot: "Hot",
  warm: "Warm",
  cold: "Cold",
  contacted: "Contacted",
  converted: "Bought",
  lost: "Lost",
};

export const CHANNEL_LABEL: Record<Channel, string> = {
  whatsapp: "WhatsApp",
  instagram: "Instagram",
  messenger: "Messenger",
};

export const SOURCE_LABEL: Record<string, string> = {
  ctwa_ad: "WhatsApp ad",
  ad_form: "Lead form",
  comment: "Comment",
  dm: "Direct message",
  website_chat: "Website chat",
};

const TIMELINE_LABEL: Record<string, string> = {
  now: "Today",
  this_week: "This week",
  this_month: "This month",
  later: "Later",
};

/** Indian digit grouping: 112999 -> ₹1,12,999 (matches the backend's format_price). */
export function formatINR(value: string | number | null | undefined): string {
  if (value == null || value === "") return "";
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return String(value);
  const whole = Math.trunc(Math.abs(n));
  const paise = Math.round((Math.abs(n) - whole) * 100);
  const digits = String(whole);
  let grouped = digits;
  if (digits.length > 3) {
    const head = digits.slice(0, -3).replace(/\B(?=(\d{2})+(?!\d))/g, ",");
    grouped = `${head},${digits.slice(-3)}`;
  }
  const sign = n < 0 ? "-" : "";
  return `${sign}₹${grouped}${paise ? `.${String(paise).padStart(2, "0")}` : ""}`;
}

/** Messaging cost: small amounts keep paise. */
export function formatCost(value: string | number): string {
  const n = Number(value);
  if (!n) return "₹0";
  return `₹${n.toFixed(2)}`;
}

/** "now", "5 min", "3 h", "Yesterday", "12 Oct" */
export function shortTime(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return "";
  const t = new Date(iso);
  const diff = (now.getTime() - t.getTime()) / 1000;
  if (diff < 60) return "now";
  if (diff < 3600) return `${Math.floor(diff / 60)} min`;
  if (diff < 86400 && t.getDate() === now.getDate()) return `${Math.floor(diff / 3600)} h`;
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (t.toDateString() === yesterday.toDateString()) return "Yesterday";
  return t.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}

export function clockTime(iso: string): string {
  return new Date(iso).toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" });
}

export function dayLabel(iso: string, now: Date = new Date()): string {
  const t = new Date(iso);
  if (t.toDateString() === now.toDateString()) return "Today";
  const y = new Date(now);
  y.setDate(now.getDate() - 1);
  if (t.toDateString() === y.toDateString()) return "Yesterday";
  return t.toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" });
}

/** "Closes in 3 h 20 min" / "Closed" for a messaging window end. */
export function windowLeft(iso: string | null, now: Date = new Date()): string {
  if (!iso) return "";
  const mins = Math.round((new Date(iso).getTime() - now.getTime()) / 60000);
  if (mins <= 0) return "Closed";
  if (mins < 60) return `Closes in ${mins} min`;
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return `Closes in ${h} h${m ? ` ${m} min` : ""}`;
}

/** Instagram and Messenger DMs carry no name, so say where the customer came from. */
export function displayName(lead: {
  name: string | null;
  contact_phone: string | null;
  preferred_channel?: Channel | null;
  erased_at?: string | null;
}): string {
  if (lead.erased_at) return "Erased customer";
  if (lead.name) return lead.name;
  if (lead.contact_phone) return lead.contact_phone;
  if (lead.preferred_channel === "instagram") return "Instagram customer";
  if (lead.preferred_channel === "messenger") return "Messenger customer";
  return "Customer";
}

/** Who is answering a lead the assistant is no longer answering. */
export function handlerText(lead: { is_human_takeover: boolean; assigned_to: string | null }, me?: string): string | null {
  if (!lead.is_human_takeover) return null;
  if (!lead.assigned_to) return "Waiting for a person";
  return lead.assigned_to === me ? "You're handling this" : `${lead.assigned_to} is handling this`;
}

/** One-line preview: the catalog list reply becomes "Options: A, B, Which one?". */
export function previewText(text: string): string {
  const template = text.match(/^\[template ([^\]]+)\]/);
  if (template) return `${TEMPLATE_LABEL[template[1]] ?? template[1]} template`;
  const lines = text
    .split("\n")
    .map((l) => l.replace(/^•\s*/, "").trim())
    .filter(Boolean);
  if (lines[0] === "Here are the options that match:") return `Options: ${lines.slice(1).join(", ")}`;
  return lines.join(" ");
}

/** Facts the customer gave, in reading order, for the lead panel. */
export function slotFacts(slots: QualifySlots): { label: string; value: string }[] {
  const facts: { label: string; value: string }[] = [];
  if (slots.product || slots.category) facts.push({ label: "Wants", value: (slots.product || slots.category)! });
  if (slots.quantity) facts.push({ label: "Quantity", value: String(slots.quantity) });
  if (slots.budget) facts.push({ label: "Budget", value: formatINR(slots.budget) });
  if (slots.timeline) facts.push({ label: "Buying", value: TIMELINE_LABEL[slots.timeline] ?? slots.timeline });
  if (slots.city) facts.push({ label: "Delivery", value: slots.city });
  if (slots.purchase_intent)
    facts.push({
      label: "Intent",
      value: { high: "Ready to buy", medium: "Comparing prices", low: "Just looking" }[slots.purchase_intent],
    });
  if (slots.sentiment === "negative") facts.push({ label: "Mood", value: "Unhappy" });
  return facts;
}

/** Split a score rationale ("wants X (+20); budget ₹15,000 (+10)") into lines with points. */
export function rationaleLines(rationale: string | null): { text: string; points: number | null }[] {
  if (!rationale || rationale === "not enough information yet") return [];
  return rationale.split("; ").map((part) => {
    const m = part.match(/^(.*) \(([+-]\d+)\)$/);
    return m ? { text: m[1], points: Number(m[2]) } : { text: part, points: null };
  });
}

/**
 * Order by real time, not by text: "…:56Z" sorts after "…:56.889Z" as text because "." < "Z"
 * (a reply showed above the voice note it answered; found in the Week 4 run). Ties keep their order.
 */
export function byTime(a: { at: string }, b: { at: string }): number {
  return Date.parse(a.at) - Date.parse(b.at);
}

/** A WhatsApp template body with {{1}}, {{2}}... filled in (missing values stay visible). */
export function fillTemplate(body: string, params: string[]): string {
  return body.replace(/\{\{\s*(\d+)\s*\}\}/g, (whole, n: string) => params[Number(n) - 1] ?? whole);
}

/** "Tue 21 Oct, 10:00 am" in the viewer's time zone. */
export function dateTime(iso: string): string {
  const t = new Date(iso);
  const day = t.toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" });
  return `${day}, ${clockTime(iso)}`;
}

export const TEMPLATE_LABEL: Record<string, string> = {
  nurture_followup_1: "Check-in",
  nurture_offer: "Offer",
  nurture_last_check: "Last reminder",
  lead_form_welcome: "Welcome",
};

const OPT_IN_SOURCE: Record<string, string> = {
  lead_form: "on the lead form",
  ctwa_message: "said yes in the chat",
  dm: "said yes in the chat",
  keyword: "sent START",
  manual: "added by hand",
};

/** What the customer agreed to, for the lead panel. */
export function consentText(lead: {
  opted_out: boolean;
  opted_out_at: string | null;
  whatsapp_opt_in: boolean;
  opt_in_source: string | null;
  opt_in_at: string | null;
  opt_in_asked_at: string | null;
  contact_phone: string | null;
}): string {
  if (lead.opted_out) {
    const when = lead.opted_out_at ? ` on ${shortDate(lead.opted_out_at)}` : "";
    return `Opted out${when}. You can still reply if they write to you.`;
  }
  if (lead.whatsapp_opt_in) {
    const how = lead.opt_in_source ? OPT_IN_SOURCE[lead.opt_in_source] ?? lead.opt_in_source : "";
    const when = lead.opt_in_at ? `, ${shortDate(lead.opt_in_at)}` : "";
    return `Agreed${how ? ` (${how}${when})` : ""}`;
  }
  if (!lead.contact_phone) return "No WhatsApp number";
  if (lead.opt_in_asked_at) return "Asked in the chat, no yes yet";
  return "Not asked yet";
}

export function shortDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}

/** "4 s", "1 min 5 s", "2 h 10 min" */
export function duration(seconds: number | null | undefined): string {
  if (seconds == null) return "-";
  if (seconds < 1) return "under 1 s";
  const s = Math.round(seconds);
  if (s < 60) return `${s} s`;
  if (s < 3600) {
    const m = Math.floor(s / 60);
    return `${m} min${s % 60 ? ` ${s % 60} s` : ""}`;
  }
  const h = Math.floor(s / 3600);
  const m = Math.round((s % 3600) / 60);
  return `${h} h${m ? ` ${m} min` : ""}`;
}

export function pct(value: number | null | undefined): string {
  return value == null ? "-" : `${Number.isInteger(value) ? value : value.toFixed(1)}%`;
}
