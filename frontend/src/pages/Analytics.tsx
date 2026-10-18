import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { BarList, ColumnChart } from "../components/charts";
import { ErrorNote, PageHeader, Spinner } from "../components/ui";
import { api } from "../lib/api";
import { CHANNEL_LABEL, duration, formatINR, pct, SOURCE_LABEL } from "../lib/format";
import type { AnalyticsCosts, AnalyticsSummary, Channel } from "../lib/types";

const PERIODS = [7, 30, 90] as const;

const STAGE_LABEL: Record<AnalyticsSummary["funnel"][number]["stage"], string> = {
  captured: "New leads",
  replied: "Wrote back",
  qualified: "Qualified",
  hot: "Hot",
  bought: "Bought",
};

const CATEGORY_LABEL: Record<string, string> = {
  marketing: "Templates (marketing)",
  utility: "Templates (utility)",
  service: "Replies in the 24-hour window",
  free_entry: "Free: ad window (72 h)",
  free: "Free: Instagram and Messenger",
};

function rupees(value: string | number | null | undefined): string {
  if (value == null) return "-";
  const n = Number(value);
  return n < 100 ? `₹${n.toFixed(2)}` : formatINR(Math.round(n));
}

function dayShort(iso: string): string {
  return new Date(`${iso}T00:00:00`).toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}

function Tile({ label, value, sub }: { label: string; value: string; sub?: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-1 bg-sheet px-5 py-4">
      <span className="text-sm text-ink-2">{label}</span>
      <span className="wide text-[28px] leading-none font-extrabold text-ink">{value}</span>
      {sub && <span className="text-xs text-ink-3">{sub}</span>}
    </div>
  );
}

function Card({ title, intro, children }: { title: string; intro?: string; children: ReactNode }) {
  return (
    <section className="flex min-w-0 flex-col gap-4 bg-sheet px-5 py-5 md:px-6">
      <div>
        <h2 className="text-[17px] font-semibold">{title}</h2>
        {intro && <p className="mt-0.5 text-sm text-ink-2">{intro}</p>}
      </div>
      {children}
    </section>
  );
}

function Facts({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="flex flex-col gap-1.5 text-sm">
      {rows.map(([k, v]) => (
        <div key={k} className="flex justify-between gap-4 border-b border-rule pb-1.5 last:border-0">
          <dt className="text-ink-2">{k}</dt>
          <dd className="tabular text-right text-ink">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Target line: "met" in words, never colour alone. */
function Target({ ok, children }: { ok: boolean | null; children: ReactNode }) {
  if (ok == null) return <span className="text-ink-3">{children}</span>;
  return (
    <span className={ok ? "text-ok" : "text-hot"}>
      {ok ? "Target met: " : "Below target: "}
      {children}
    </span>
  );
}

export function Analytics() {
  const [days, setDays] = useState<(typeof PERIODS)[number]>(30);
  const summary = useQuery({
    queryKey: ["analytics", "summary", days],
    queryFn: () => api<AnalyticsSummary>(`/api/analytics/summary?days=${days}`),
    placeholderData: keepPreviousData,
  });
  const costs = useQuery({
    queryKey: ["analytics", "costs", days],
    queryFn: () => api<AnalyticsCosts>(`/api/analytics/costs?days=${days}`),
    placeholderData: keepPreviousData,
  });

  const s = summary.data;
  const c = costs.data;
  const refreshing = summary.isPlaceholderData || costs.isPlaceholderData;

  return (
    <div className="h-full overflow-y-auto bg-steel">
      <PageHeader title="Analytics">
        <div role="radiogroup" aria-label="Period" className="flex rounded-md border border-rule bg-sheet p-0.5">
          {PERIODS.map((d) => (
            <button
              key={d}
              type="button"
              role="radio"
              aria-checked={d === days}
              onClick={() => setDays(d)}
              className={`rounded px-3 py-1.5 text-sm font-medium ${d === days ? "bg-ink text-sheet" : "text-ink-2 hover:text-ink"}`}
            >
              {d} days
            </button>
          ))}
        </div>
      </PageHeader>

      {summary.isPending || costs.isPending ? (
        <Spinner label="Loading numbers" />
      ) : summary.isError || costs.isError ? (
        <div className="p-6">
          <ErrorNote error={summary.error ?? costs.error} />
        </div>
      ) : s && c ? (
        <div className={`flex flex-col gap-px bg-rule transition-opacity ${refreshing ? "opacity-60" : ""}`}>
          <div className="grid grid-cols-2 gap-px lg:grid-cols-5">
            <Tile label="New leads" value={String(s.leads.total)} sub={`${s.leads.today} today, ${s.leads.last_7_days} in the last 7 days`} />
            <Tile label="Wrote back" value={pct(s.rates.reply_rate_pct)} sub="after our first message" />
            <Tile label="Bought" value={pct(s.rates.conversion_pct)} sub={`${s.funnel[4].count} of ${s.leads.total} leads`} />
            <Tile
              label="First reply"
              value={duration(s.first_response.median_seconds)}
              sub={`median; ${pct(s.first_response.within_target_pct)} within a minute`}
            />
            <div className="col-span-2 grid lg:col-span-1">
              <Tile label="Messaging cost" value={rupees(c.total)} sub={`${rupees(c.per_lead)} per lead`} />
            </div>
          </div>

          <Card title="New leads per day" intro={`Last ${s.period.days} days, ${s.period.timezone} time.`}>
            <ColumnChart
              title="New leads per day"
              unit="leads"
              data={s.daily.map((d) => ({
                key: d.date,
                label: dayShort(d.date),
                value: d.leads,
                display: `${d.leads} ${d.leads === 1 ? "lead" : "leads"}${d.hot ? `, ${d.hot} hot` : ""}`,
              }))}
            />
          </Card>

          <div className="grid gap-px lg:grid-cols-2">
            <Card title="From lead to sale" intro="How far this period's leads got.">
              <BarList
                rows={s.funnel.map((f) => ({
                  label: STAGE_LABEL[f.stage],
                  value: f.count,
                  display:
                    f.stage === "captured" || !s.leads.total
                      ? String(f.count)
                      : `${f.count} (${pct(Math.round((1000 * f.count) / s.leads.total) / 10)})`,
                }))}
              />
              <p className="text-xs text-ink-3">
                Qualified and hot use the thresholds in Settings. Of the leads you closed, {pct(s.rates.win_rate_pct)} bought.
              </p>
            </Card>

            <Card title="Where leads came from">
              <BarList
                rows={Object.entries(s.leads.by_source).map(([k, v]) => ({
                  label: SOURCE_LABEL[k] ?? k,
                  value: v,
                  display: String(v),
                }))}
              />
              <p className="text-sm text-ink-2">
                {Object.entries(s.leads.by_channel)
                  .map(([k, v]) => `${CHANNEL_LABEL[k as Channel] ?? k} ${v}`)
                  .join(", ") || "No leads yet."}
              </p>
            </Card>

            <Card title="Speed" intro="From the customer's first message (or the lead form) to our first reply.">
              <Facts
                rows={[
                  ["Median", duration(s.first_response.median_seconds)],
                  ["Slowest 5%", duration(s.first_response.p95_seconds)],
                  [
                    "Within a minute",
                    <Target
                      key="t"
                      ok={s.first_response.within_target_pct == null ? null : s.first_response.within_target_pct >= 95}
                    >
                      {pct(s.first_response.within_target_pct)} (goal 95%)
                    </Target>,
                  ],
                  ["Never answered", String(s.first_response.unanswered)],
                  ["Handed to a person", String(s.handoffs)],
                ]}
              />
            </Card>

            <Card title="Hot labels" intro='Answer "Is this lead really hot?" on hot leads to measure this.'>
              <Facts
                rows={[
                  ["Answered", String(s.hot_label_feedback.answered)],
                  ["You agreed", String(s.hot_label_feedback.agreed)],
                  [
                    "Agreement",
                    <Target
                      key="t"
                      ok={s.hot_label_feedback.agreement_pct == null ? null : s.hot_label_feedback.agreement_pct >= s.hot_label_feedback.target_pct}
                    >
                      {pct(s.hot_label_feedback.agreement_pct)} (goal {s.hot_label_feedback.target_pct}%)
                    </Target>,
                  ],
                ]}
              />
            </Card>

            <Card title="Follow-ups and consent">
              <Facts
                rows={[
                  ["Follow-ups sent", String(s.follow_ups.sent)],
                  ["Leads followed up", String(s.follow_ups.leads_followed_up)],
                  ["Wrote back within 7 days", String(s.follow_ups.re_engaged)],
                  [
                    "Agreed to offers",
                    `${s.follow_ups.opt_ins}${
                      s.follow_ups.opt_ins
                        ? ` (${Object.entries(s.follow_ups.opt_ins_by_source)
                            .map(([k, v]) => `${k === "lead_form" ? "lead form" : k === "keyword" ? "START" : "in chat"} ${v}`)
                            .join(", ")})`
                        : ""
                    }`,
                  ],
                  ["Opted out", String(s.follow_ups.opt_outs)],
                ]}
              />
            </Card>

            <Card title="Messaging cost" intro={c.note}>
              <Facts
                rows={[
                  ["Total", rupees(c.total)],
                  ["Per lead", rupees(c.per_lead)],
                  ["Per qualified lead", rupees(c.per_qualified_lead)],
                  ["Per sale", rupees(c.per_bought_lead)],
                  ["Saved by the free ad window", rupees(c.saved_by_free_entry_window)],
                ]}
              />
            </Card>
          </div>

          <Card title="Cost per day" intro="Estimated WhatsApp cost of messages sent each day.">
            <ColumnChart
              title="Messaging cost per day"
              unit="rupees"
              data={c.daily.map((d) => ({ key: d.date, label: dayShort(d.date), value: Number(d.cost), display: rupees(d.cost) }))}
            />
            <table className="w-full max-w-xl text-left text-sm">
              <thead className="text-xs text-ink-3">
                <tr>
                  <th className="py-1 font-medium">Kind of message</th>
                  <th className="py-1 text-right font-medium">Messages</th>
                  <th className="py-1 text-right font-medium">Cost</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(c.by_category).map(([k, v]) => (
                  <tr key={k} className="border-t border-rule">
                    <td className="py-1.5 text-ink-2">{CATEGORY_LABEL[k] ?? k}</td>
                    <td className="tabular py-1.5 text-right">{v.messages}</td>
                    <td className="tabular py-1.5 text-right">{rupees(v.cost)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </div>
      ) : null}
    </div>
  );
}
