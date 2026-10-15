import { HEAT_VAR, heatOf, STATUS_LABEL, type Heat } from "../lib/format";
import type { LeadStatus } from "../lib/types";

const HEAT_WORD: Record<Heat, string> = { hot: "Hot", warm: "Warm", cold: "Cold", none: "Not scored" };

/** The score in the expanded cut, coloured by temperature. */
export function Score({ status, score, size = "md" }: { status: LeadStatus; score: number | null; size?: "md" | "xl" }) {
  const heat = heatOf(status, score);
  const cls = size === "xl" ? "text-6xl leading-none" : "text-xl leading-none";
  return (
    <span
      className={`wide tabular font-black ${cls}`}
      style={{ color: heat === "none" ? "var(--ink-3)" : HEAT_VAR[heat] }}
      aria-label={`Score ${score ?? "none"}, ${HEAT_WORD[heat]}`}
    >
      {score ?? "–"}
    </span>
  );
}

/** 0-100 bar with the hot/warm thresholds marked. */
export function HeatBar({ status, score, warmAt = 40, hotAt = 70 }: { status: LeadStatus; score: number | null; warmAt?: number; hotAt?: number }) {
  const heat = heatOf(status, score);
  const pct = Math.max(0, Math.min(100, score ?? 0));
  return (
    <div className="relative h-2.5 w-full overflow-hidden rounded-full bg-sheet-2" aria-hidden>
      <div className="h-full rounded-full transition-[width] duration-500" style={{ width: `${pct}%`, background: HEAT_VAR[heat] }} />
      <span className="absolute inset-y-0 w-px bg-rule" style={{ left: `${warmAt}%` }} />
      <span className="absolute inset-y-0 w-px bg-rule" style={{ left: `${hotAt}%` }} />
    </div>
  );
}

export function StatusWord({ status, score }: { status: LeadStatus; score: number | null }) {
  const heat = heatOf(status, score);
  const closed = status === "converted" || status === "lost" || status === "contacted";
  return (
    <span className="inline-flex items-center gap-1.5 text-sm font-medium text-ink-2">
      <span className="size-2 rounded-full" style={{ background: closed ? "var(--ink-3)" : HEAT_VAR[heat] }} aria-hidden />
      {STATUS_LABEL[status]}
    </span>
  );
}
