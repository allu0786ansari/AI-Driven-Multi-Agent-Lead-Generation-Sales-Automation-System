import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Plus, Trash2 } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { useToast } from "../components/toasts";
import { Button, ErrorNote, Field, Input, PageHeader, Select, Spinner } from "../components/ui";
import { api } from "../lib/api";
import { dateTime, displayName, TEMPLATE_LABEL } from "../lib/format";
import type { NurtureConfig, NurtureParam, Product, QualificationConfig, Slot, Template, Upcoming } from "../lib/types";

const WEIGHT_LABEL: Record<string, string> = {
  product: "Names a product",
  budget_known: "Gives a budget",
  budget_fits: "Budget covers the product",
  timeline_soon: "Buying today or this week",
  timeline_this_month: "Buying this month",
  timeline_later: "Buying later",
  quantity_known: "Says how many",
  bulk: "Bulk order",
  city: "Gives a delivery city",
  intent_high: "Ready to buy",
  intent_medium: "Asks about price or stock",
  engaged: "Sent 2 or more messages",
  negative_sentiment: "Sounds unhappy",
};

const SLOT_LABEL: Record<Slot, string> = {
  product: "Product",
  quantity: "Quantity",
  timeline: "When they'll buy",
  city: "Delivery city",
  budget: "Budget",
};

function Block({ title, intro, children }: { title: string; intro: string; children: ReactNode }) {
  return (
    <section className="border-b border-rule px-5 py-8 md:px-8">
      <h2 className="text-[19px] font-semibold">{title}</h2>
      <p className="mt-1 max-w-prose text-sm text-ink-2">{intro}</p>
      <div className="mt-5">{children}</div>
    </section>
  );
}

export function Settings() {
  return (
    <div className="h-full overflow-y-auto bg-sheet">
      <PageHeader title="Settings" />
      <div className="max-w-3xl">
        <Qualification />
        <FollowUps />
        <ContentLinks />
        <System />
      </div>
    </div>
  );
}

function Qualification() {
  const qc = useQueryClient();
  const toast = useToast();
  const remote = useQuery({ queryKey: ["settings", "qualification"], queryFn: () => api<QualificationConfig>("/api/settings/qualification") });
  const [cfg, setCfg] = useState<QualificationConfig | null>(null);
  useEffect(() => {
    if (remote.data) setCfg(structuredClone(remote.data));
  }, [remote.data]);
  const save = useMutation({
    mutationFn: (c: QualificationConfig) => api<QualificationConfig>("/api/settings/qualification", { method: "PUT", json: c }),
    onSuccess: (c) => {
      qc.setQueryData(["settings", "qualification"], c);
      toast.show("Saved. New scores apply from each customer's next message.");
    },
  });

  if (remote.isPending || !cfg) return <Spinner />;
  if (remote.isError) return <ErrorNote error={remote.error} />;
  const dirty = JSON.stringify(cfg) !== JSON.stringify(remote.data);
  const unused = (Object.keys(SLOT_LABEL) as Slot[]).filter((s) => !cfg.questions.some((q) => q.slot === s));
  const move = (i: number, d: -1 | 1) =>
    setCfg((c) => {
      const qs = [...c!.questions];
      [qs[i], qs[i + d]] = [qs[i + d], qs[i]];
      return { ...c!, questions: qs };
    });

  return (
    <>
      <Block
        title="Questions the assistant asks"
        intro="Each reply ends with the first question in this list that the customer hasn't answered yet. Put the most useful first."
      >
        <ol className="flex flex-col gap-2">
          {cfg.questions.map((q, i) => (
            <li key={q.slot} className="flex items-center gap-2">
              <span className="tabular w-5 text-sm text-ink-3">{i + 1}</span>
              <span className="w-32 shrink-0 text-sm font-medium">{SLOT_LABEL[q.slot]}</span>
              <Input
                aria-label={`Question about ${SLOT_LABEL[q.slot]}`}
                value={q.question}
                onChange={(e) => setCfg((c) => ({ ...c!, questions: c!.questions.map((x, j) => (j === i ? { ...x, question: e.target.value } : x)) }))}
                className="h-9"
              />
              <Button tone="ghost" aria-label="Move up" disabled={i === 0} onClick={() => move(i, -1)} className="px-2">
                <ArrowUp className="size-4" />
              </Button>
              <Button tone="ghost" aria-label="Move down" disabled={i === cfg.questions.length - 1} onClick={() => move(i, 1)} className="px-2">
                <ArrowDown className="size-4" />
              </Button>
              <Button
                tone="ghost"
                aria-label={`Stop asking about ${SLOT_LABEL[q.slot]}`}
                onClick={() => setCfg((c) => ({ ...c!, questions: c!.questions.filter((_, j) => j !== i) }))}
                className="px-2"
              >
                <Trash2 className="size-4" />
              </Button>
            </li>
          ))}
        </ol>
        {unused.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-2">
            {unused.map((s) => (
              <Button
                key={s}
                icon={<Plus className="size-4" aria-hidden />}
                onClick={() => setCfg((c) => ({ ...c!, questions: [...c!.questions, { slot: s, question: `Your question about ${SLOT_LABEL[s].toLowerCase()}?` }] }))}
              >
                Ask about {SLOT_LABEL[s].toLowerCase()}
              </Button>
            ))}
          </div>
        )}
      </Block>

      <Block
        title="What makes a lead hot"
        intro="Each thing the customer tells us adds points, up to 100. Leads at or above the hot line alert you right away."
      >
        <div className="grid max-w-md grid-cols-2 gap-4">
          <Field label="Hot from" htmlFor="hot">
            <Input id="hot" type="number" min={1} max={100} value={cfg.hot_threshold} onChange={(e) => setCfg({ ...cfg, hot_threshold: Number(e.target.value) })} />
          </Field>
          <Field label="Warm from" htmlFor="warm">
            <Input id="warm" type="number" min={0} max={99} value={cfg.warm_threshold} onChange={(e) => setCfg({ ...cfg, warm_threshold: Number(e.target.value) })} />
          </Field>
        </div>
        <table className="mt-6 w-full max-w-md text-sm">
          <thead>
            <tr className="border-b border-rule text-left text-ink-3">
              <th className="py-2 font-medium">The customer…</th>
              <th className="w-24 py-2 text-right font-medium">Points</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(cfg.weights).map(([k, v]) => (
              <tr key={k} className="border-b border-rule">
                <td className="py-1.5">
                  {WEIGHT_LABEL[k] ?? k}
                  {k === "bulk" && <span className="text-ink-3"> (at least {cfg.bulk_threshold})</span>}
                </td>
                <td className="py-1.5 text-right">
                  <Input
                    aria-label={`Points for: ${WEIGHT_LABEL[k] ?? k}`}
                    type="number"
                    min={-100}
                    max={100}
                    value={v}
                    onChange={(e) => setCfg({ ...cfg, weights: { ...cfg.weights, [k]: Number(e.target.value) } })}
                    className="tabular h-8 w-20 text-right"
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="mt-6 flex items-center gap-3">
          <Button tone="primary" disabled={!dirty} busy={save.isPending} onClick={() => save.mutate(cfg)}>
            Save scoring and questions
          </Button>
          {dirty && (
            <Button tone="ghost" onClick={() => setCfg(structuredClone(remote.data!))}>
              Discard changes
            </Button>
          )}
        </div>
        {save.isError && (
          <div className="mt-3">
            <ErrorNote error={save.error} />
          </div>
        )}
      </Block>
    </>
  );
}

const PARAM_LABEL: Record<NurtureParam, string> = { first_name: "first name", product: "product", offer: "offer" };
const PARAMS_BY_COUNT: NurtureParam[][] = [[], ["first_name"], ["first_name", "product"], ["first_name", "product", "offer"]];

function FollowUps() {
  const qc = useQueryClient();
  const toast = useToast();
  const remote = useQuery({ queryKey: ["settings", "nurture"], queryFn: () => api<NurtureConfig>("/api/settings/nurture") });
  const templates = useQuery({ queryKey: ["templates"], queryFn: () => api<{ templates: Template[] }>("/api/templates"), staleTime: 600_000 });
  const upcoming = useQuery({ queryKey: ["nurture", "upcoming"], queryFn: () => api<Upcoming>("/api/nurture/upcoming") });
  const [cfg, setCfg] = useState<NurtureConfig | null>(null);
  useEffect(() => {
    if (remote.data) setCfg(structuredClone(remote.data));
  }, [remote.data]);
  const save = useMutation({
    mutationFn: (c: NurtureConfig) => api<NurtureConfig>("/api/settings/nurture", { method: "PUT", json: c }),
    onSuccess: (c) => {
      qc.setQueryData(["settings", "nurture"], c);
      qc.invalidateQueries({ queryKey: ["nurture"] });
      toast.show("Saved. Every lead's next follow-up is worked out again within a few minutes.");
    },
  });
  const run = useMutation({
    mutationFn: () => api<Record<string, number>>("/api/nurture/run", { method: "POST" }),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["nurture"] });
      toast.show(r.sent ? `${r.sent} follow-up${r.sent > 1 ? "s" : ""} sent.` : "Nothing due right now.");
    },
    onError: (e: Error) => toast.show(e.message, { tone: "error" }),
  });

  if (remote.isPending || !cfg) return <Spinner />;
  if (remote.isError) return <ErrorNote error={remote.error} />;
  const dirty = JSON.stringify(cfg) !== JSON.stringify(remote.data);
  const names = [...new Set([...(templates.data?.templates ?? []).map((t) => t.name), ...cfg.steps.map((st) => st.template)])];
  const paramCount = (name: string) => templates.data?.templates.find((t) => t.name === name)?.param_count;
  const setStep = (i: number, patch: Partial<NurtureConfig["steps"][number]>) =>
    setCfg((c) => ({ ...c!, steps: c!.steps.map((st, j) => (j === i ? { ...st, ...patch } : st)) }));

  return (
    <Block
      title="Follow-ups"
      intro="When a customer who agreed to offers goes quiet, an approved WhatsApp template reminds them. Any message from the customer stops the sequence; it starts again if they go quiet again. Never sent to someone who opted out, a lead marked Contacted, Bought or Lost, or while you are handling the conversation."
    >
      <label className="flex items-center gap-2 text-sm font-medium">
        <input type="checkbox" checked={cfg.enabled} onChange={(e) => setCfg({ ...cfg, enabled: e.target.checked })} className="size-4" />
        Send follow-ups
      </label>

      <ol className={`mt-5 flex flex-col gap-3 ${cfg.enabled ? "" : "opacity-50"}`}>
        {cfg.steps.map((st, i) => (
          <li key={i} className="flex flex-wrap items-center gap-2 text-sm">
            <span className="tabular w-5 text-ink-3">{i + 1}</span>
            <span>After</span>
            <Input
              aria-label={`Follow-up ${i + 1}: days without a reply`}
              type="number"
              min={0.5}
              max={60}
              step={0.5}
              value={st.after_days}
              onChange={(e) => setStep(i, { after_days: Number(e.target.value) })}
              className="tabular h-9 w-20 text-right"
            />
            <span>days quiet, send</span>
            <Select
              aria-label={`Follow-up ${i + 1}: template`}
              value={st.template}
              onChange={(e) => {
                const n = paramCount(e.target.value);
                setStep(i, { template: e.target.value, ...(n != null && n <= 3 ? { params: PARAMS_BY_COUNT[n] } : {}) });
              }}
              className="h-9 w-56"
            >
              {names.map((n) => (
                <option key={n} value={n}>
                  {TEMPLATE_LABEL[n] ? `${TEMPLATE_LABEL[n]} (${n})` : n}
                </option>
              ))}
            </Select>
            <Button
              tone="ghost"
              aria-label={`Remove follow-up ${i + 1}`}
              disabled={cfg.steps.length === 1}
              onClick={() => setCfg({ ...cfg, steps: cfg.steps.filter((_, j) => j !== i) })}
              className="px-2"
            >
              <Trash2 className="size-4" />
            </Button>
            <span className="basis-full pl-7 text-xs text-ink-3">
              Fills in: {st.params.map((x) => PARAM_LABEL[x]).join(", ") || "nothing"}
              {st.params.includes("offer") && !cfg.offer_text.trim() ? ". Skipped until you add an offer below." : ""}
            </span>
          </li>
        ))}
      </ol>
      {cfg.steps.length < 5 && (
        <Button
          className="mt-3"
          icon={<Plus className="size-4" aria-hidden />}
          onClick={() => {
            const last = cfg.steps[cfg.steps.length - 1];
            setCfg({ ...cfg, steps: [...cfg.steps, { after_days: (last?.after_days ?? 0) + 3, template: last?.template ?? "nurture_followup_1", params: ["first_name", "product"] }] });
          }}
        >
          Add a follow-up
        </Button>
      )}

      <div className="mt-6 grid max-w-xl gap-4 sm:grid-cols-2">
        <Field label="Offer for the offer template" htmlFor="offer" hint={'Fills the offer, e.g. "free delivery in Chennai this week".'}>
          <Input id="offer" value={cfg.offer_text} maxLength={200} onChange={(e) => setCfg({ ...cfg, offer_text: e.target.value })} />
        </Field>
        <Field label="Most follow-ups per customer" htmlFor="maxper" hint="Counted over all time, not per quiet spell.">
          <Input id="maxper" type="number" min={1} max={20} value={cfg.max_per_lead} onChange={(e) => setCfg({ ...cfg, max_per_lead: Number(e.target.value) })} />
        </Field>
        <Field label="Don't start after (days quiet)" htmlFor="maxquiet" hint="Customers quiet longer than this get no new follow-ups.">
          <Input id="maxquiet" type="number" min={1} max={365} value={cfg.max_quiet_days} onChange={(e) => setCfg({ ...cfg, max_quiet_days: Number(e.target.value) })} />
        </Field>
        <div className="hidden sm:block" />
        <Field label="Send from (hour)" htmlFor="fromh" hint="Business time zone, 24-hour clock.">
          <Input id="fromh" type="number" min={0} max={23} value={cfg.send_from_hour} onChange={(e) => setCfg({ ...cfg, send_from_hour: Number(e.target.value) })} />
        </Field>
        <Field label="Until (hour)" htmlFor="untilh">
          <Input id="untilh" type="number" min={1} max={24} value={cfg.send_until_hour} onChange={(e) => setCfg({ ...cfg, send_until_hour: Number(e.target.value) })} />
        </Field>
      </div>

      <div className="mt-6 flex items-center gap-3">
        <Button tone="primary" disabled={!dirty} busy={save.isPending} onClick={() => save.mutate(cfg)}>
          Save follow-ups
        </Button>
        {dirty && (
          <Button tone="ghost" onClick={() => setCfg(structuredClone(remote.data!))}>
            Discard changes
          </Button>
        )}
      </div>
      {save.isError && (
        <div className="mt-3">
          <ErrorNote error={save.error} />
        </div>
      )}

      <div className="mt-8 flex flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-sm font-semibold">Planned next</h3>
          <Button onClick={() => run.mutate()} busy={run.isPending}>
            Check now
          </Button>
        </div>
        {upcoming.data && upcoming.data.upcoming.length > 0 ? (
          <ul className="flex flex-col text-sm">
            {upcoming.data.upcoming.map((u) => (
              <li key={u.lead_id} className="flex flex-wrap justify-between gap-x-4 border-b border-rule py-1.5 last:border-0">
                <span>
                  {displayName({ name: u.name, contact_phone: null })}
                  {u.product_interest && <span className="text-ink-3"> about {u.product_interest}</span>}
                </span>
                <span className="tabular text-ink-2">
                  Follow-up {u.step}, {dateTime(u.due_at)}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-ink-3">Nothing planned. Follow-ups are planned for customers who agreed to offers and went quiet.</p>
        )}
        {upcoming.data && <p className="text-xs text-ink-3">{upcoming.data.sent_last_7_days} sent in the last 7 days.</p>}
      </div>
    </Block>
  );
}

function ContentLinks() {
  const qc = useQueryClient();
  const toast = useToast();
  const remote = useQuery({ queryKey: ["settings", "links"], queryFn: () => api<{ links: Record<string, string> }>("/api/settings/content-products") });
  const products = useQuery({ queryKey: ["products", ""], queryFn: () => api<Product[]>("/api/products") });
  const [rows, setRows] = useState<{ id: string; sku: string }[]>([]);
  useEffect(() => {
    if (remote.data) setRows(Object.entries(remote.data.links).map(([id, sku]) => ({ id, sku })));
  }, [remote.data]);
  const save = useMutation({
    mutationFn: () =>
      api<{ links: Record<string, string> }>("/api/settings/content-products", {
        method: "PUT",
        json: { links: Object.fromEntries(rows.filter((r) => r.id.trim() && r.sku).map((r) => [r.id.trim(), r.sku])) },
      }),
    onSuccess: (d) => {
      qc.setQueryData(["settings", "links"], d);
      toast.show("Saved. Comments on these posts are answered about the linked product.");
    },
  });

  return (
    <Block
      title="Posts and ads"
      intro='Link a post, reel or ad to the product it shows, so "How much is this?" gets the right price. The ID is shown in the lead details as "Details" (for example "Instagram comment on post 1790…").'
    >
      {remote.isPending || products.isPending ? (
        <Spinner />
      ) : (
        <>
          {rows.length === 0 && <p className="mb-3 text-sm text-ink-3">No posts linked yet.</p>}
          <div className="flex flex-col gap-2">
            {rows.map((r, i) => (
              <div key={i} className="flex flex-wrap items-center gap-2">
                <Input
                  aria-label="Post, reel or ad ID"
                  placeholder="Post, reel or ad ID"
                  value={r.id}
                  onChange={(e) => setRows((rs) => rs.map((x, j) => (j === i ? { ...x, id: e.target.value } : x)))}
                  className="h-9 w-56"
                />
                <Select
                  aria-label="Product"
                  value={r.sku}
                  onChange={(e) => setRows((rs) => rs.map((x, j) => (j === i ? { ...x, sku: e.target.value } : x)))}
                  className="h-9 min-w-0 flex-1"
                >
                  <option value="">Choose a product</option>
                  {products.data?.map((p) => (
                    <option key={p.sku} value={p.sku}>
                      {p.name}
                    </option>
                  ))}
                </Select>
                <Button tone="ghost" aria-label="Remove link" onClick={() => setRows((rs) => rs.filter((_, j) => j !== i))} className="px-2">
                  <Trash2 className="size-4" />
                </Button>
              </div>
            ))}
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button icon={<Plus className="size-4" aria-hidden />} onClick={() => setRows((rs) => [...rs, { id: "", sku: "" }])}>
              Link a post
            </Button>
            <Button tone="primary" busy={save.isPending} onClick={() => save.mutate()}>
              Save links
            </Button>
          </div>
          {save.isError && (
            <div className="mt-3">
              <ErrorNote error={save.error} />
            </div>
          )}
        </>
      )}
    </Block>
  );
}

type Health = { status: string; version: string; llm_provider: string; database: string; llm?: string };

function System() {
  const health = useQuery({ queryKey: ["health"], queryFn: () => api<Health>("/api/health?deep=true") });
  const h = health.data;
  return (
    <Block title="System" intro="How this installation is running. Change these in backend/.env and restart the server.">
      {h ? (
        <dl className="grid max-w-md grid-cols-[10rem_1fr] gap-y-1.5 text-sm">
          <dt className="text-ink-3">Version</dt>
          <dd>{h.version}</dd>
          <dt className="text-ink-3">Database</dt>
          <dd>{h.database === "ok" ? "Working" : "Not reachable"}</dd>
          <dt className="text-ink-3">AI model</dt>
          <dd>
            {h.llm_provider === "fake" ? "Test mode (replies built from the product list only)" : "Ollama"}
            {h.llm && h.llm !== "ok" && <span className="text-danger"> - {h.llm}</span>}
          </dd>
        </dl>
      ) : (
        <Spinner />
      )}
    </Block>
  );
}
