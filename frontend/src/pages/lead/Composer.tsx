import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Send } from "lucide-react";
import { useEffect, useMemo, useState, type FormEvent, type KeyboardEvent } from "react";
import { useToast } from "../../components/toasts";
import { Button, Field, Input, Select, Textarea } from "../../components/ui";
import { api } from "../../lib/api";
import { CHANNEL_LABEL, windowLeft } from "../../lib/format";
import type { Channel, LeadDetail, SendResult, Template } from "../../lib/types";

function newId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID().replace(/-/g, "")
    : `${Date.now()}${Math.random().toString(16).slice(2)}`;
}

/** Where and how you can reply right now, with the reason when you can't type freely. */
export function Composer({ lead }: { lead: LeadDetail }) {
  const qc = useQueryClient();
  const toast = useToast();
  const options = lead.reply_options;
  const [channel, setChannel] = useState<Channel | null>(
    () => (options.find((o) => o.channel === lead.default_channel) ?? options[0])?.channel ?? null,
  );
  useEffect(() => {
    if (!options.some((o) => o.channel === channel)) setChannel(options[0]?.channel ?? null);
  }, [options, channel]);
  const option = options.find((o) => o.channel === channel);

  const [text, setText] = useState("");
  const [mode, setMode] = useState<"text" | "template">("text");
  // One id per message being written: a double click or retry never sends twice.
  const [clientId, setClientId] = useState(newId);

  const send = useMutation({
    mutationFn: (body: object) =>
      api<SendResult>(`/api/leads/${lead.lead_id}/message`, { method: "POST", json: { ...body, channel, client_message_id: clientId } }),
    onSuccess: (r) => {
      if (r.decision === "queued") {
        setText("");
        setClientId(newId());
        setMode("text");
      } else {
        toast.show(r.reason || "The message was not sent.", { tone: "error" });
      }
      qc.invalidateQueries({ queryKey: ["lead", lead.lead_id] });
      qc.invalidateQueries({ queryKey: ["leads"] });
      qc.invalidateQueries({ queryKey: ["counts"] });
    },
    onError: (e: Error) => toast.show(e.message, { tone: "error" }),
  });

  if (!option) {
    return (
      <div className="border-t border-rule bg-sheet px-4 py-4 text-sm text-ink-2 md:px-8">
        There is no way to message this customer yet. They need to write to you on WhatsApp, Instagram or Messenger first.
      </div>
    );
  }

  const submit = (e?: FormEvent) => {
    e?.preventDefault();
    if (!text.trim() || send.isPending) return;
    send.mutate({ text });
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) submit();
  };

  return (
    <div className="border-t border-rule bg-sheet px-4 py-3 md:px-8">
      <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-2">
        {options.length > 1 ? (
          <label className="flex items-center gap-2">
            Reply on
            <Select
              value={channel ?? ""}
              onChange={(e) => setChannel(e.target.value as Channel)}
              className="h-8 w-auto py-0 text-sm"
              aria-label="Reply on"
            >
              {options.map((o) => (
                <option key={o.channel} value={o.channel}>
                  {CHANNEL_LABEL[o.channel]}
                </option>
              ))}
            </Select>
          </label>
        ) : (
          <span>Reply on {CHANNEL_LABEL[option.channel]}</span>
        )}
        {option.free_text && option.open_until && <span>{windowLeft(option.open_until)}</span>}
        {!lead.is_human_takeover && option.free_text && <span>Sending a message pauses the assistant for this customer.</span>}
      </div>

      {mode === "template" ? (
        <TemplateForm lead={lead} busy={send.isPending} onCancel={() => setMode("text")} onSend={(t) => send.mutate({ template: t })} />
      ) : option.free_text ? (
        <form onSubmit={submit} className="flex items-end gap-2">
          <Textarea
            aria-label="Your message"
            placeholder={`Write to ${lead.name ?? "the customer"}`}
            rows={2}
            maxLength={4096}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={onKey}
            className="max-h-48 min-h-[2.75rem] resize-y"
          />
          <Button type="submit" tone="primary" busy={send.isPending} disabled={!text.trim()} icon={<Send className="size-4" aria-hidden />} className="h-11">
            Send
          </Button>
        </form>
      ) : (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-md bg-sheet-2 px-3 py-2.5">
          <p className="text-sm text-ink-2">{option.reason}</p>
          {option.templates && (
            <Button onClick={() => setMode("template")} tone="primary">
              Send a template
            </Button>
          )}
        </div>
      )}
      {option.free_text && option.templates && mode === "text" && (
        <button type="button" onClick={() => setMode("template")} className="mt-2 text-xs text-ink-2 underline-offset-2 hover:text-ink hover:underline">
          Send an approved template instead
        </button>
      )}
    </div>
  );
}

function TemplateForm({
  lead,
  busy,
  onCancel,
  onSend,
}: {
  lead: LeadDetail;
  busy: boolean;
  onCancel: () => void;
  onSend: (t: { name: string; params: string[]; category: string }) => void;
}) {
  const templates = useQuery({ queryKey: ["templates"], queryFn: () => api<{ templates: Template[]; source: string; error: string | null }>("/api/templates") });
  const list = templates.data?.templates ?? [];
  const [name, setName] = useState("");
  const tpl = useMemo(() => list.find((t) => t.name === name) ?? list[0], [list, name]);
  const [params, setParams] = useState<string[]>([]);
  useEffect(() => {
    if (!tpl) return;
    const first = (lead.name ?? "").split(" ")[0];
    const defaults = [first, lead.product_interest ?? ""];
    setParams(Array.from({ length: tpl.param_count }, (_, i) => defaults[i] ?? ""));
  }, [tpl, lead.name, lead.product_interest]);

  if (templates.isPending) return <p className="text-sm text-ink-3">Loading templates</p>;
  if (!tpl) return <p className="text-sm text-ink-2">No templates found. Add them in WhatsApp Manager and get them approved first.</p>;
  const marketingWithoutOptIn = tpl.category === "marketing" && !lead.whatsapp_opt_in;

  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        onSend({ name: tpl.name, params, category: tpl.category === "utility" ? "utility" : "marketing" });
      }}
    >
      <Field label="Template" htmlFor="tpl">
        <Select id="tpl" value={tpl.name} onChange={(e) => setName(e.target.value)}>
          {list.map((t) => (
            <option key={t.name} value={t.name}>
              {t.name} ({t.category}, {t.status.toLowerCase()})
            </option>
          ))}
        </Select>
      </Field>
      {tpl.body && <p className="msg-text rounded-md bg-sheet-2 px-3 py-2 text-sm text-ink-2">{tpl.body}</p>}
      {params.map((v, i) => (
        <Field key={i} label={`Value for {{${i + 1}}}`} htmlFor={`p${i}`}>
          <Input id={`p${i}`} required value={v} onChange={(e) => setParams((ps) => ps.map((x, j) => (j === i ? e.target.value : x)))} />
        </Field>
      ))}
      {marketingWithoutOptIn && (
        <p className="text-sm text-danger">This customer has not agreed to marketing messages on WhatsApp, so this template will be blocked.</p>
      )}
      <div className="flex gap-2">
        <Button type="submit" tone="primary" busy={busy} disabled={marketingWithoutOptIn}>
          Send template
        </Button>
        <Button onClick={onCancel} tone="ghost">
          Cancel
        </Button>
      </div>
      {templates.data?.source === "settings" && (
        <p className="text-xs text-ink-3">
          Approval status is not checked in test mode. With a live WhatsApp connection the list comes from WhatsApp Manager.
        </p>
      )}
    </form>
  );
}
