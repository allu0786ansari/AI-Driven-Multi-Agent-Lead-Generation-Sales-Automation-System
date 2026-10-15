import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../components/toasts";
import { Select } from "../components/ui";
import type { LeadDetail } from "../lib/types";
import { Composer } from "./lead/Composer";
import { Conversation } from "./lead/Conversation";
import { parseAttributes } from "./Products";

afterEach(() => vi.unstubAllGlobals());

function wrap(children: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <ToastProvider>{children}</ToastProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const base: LeadDetail = {
  lead_id: "L1",
  name: "Priya K",
  contact_phone: "+919811100000",
  source: "dm",
  product_interest: "Air Fryers",
  status: "hot",
  qualification_score: 82,
  conversation_summary: null,
  preferred_channel: "whatsapp",
  assigned_to: null,
  whatsapp_opt_in: false,
  opted_out: false,
  is_human_takeover: false,
  messaging_cost_estimate: "0.12",
  created_at: "2026-10-15T09:00:00Z",
  last_activity_at: "2026-10-15T09:05:00Z",
  last_message_text: null,
  last_message_at: null,
  last_message_direction: null,
  last_message_sender: null,
  last_message_channel: null,
  unread_alerts: 0,
  contact_email: null,
  source_detail: null,
  meta_lead_id: null,
  ad_id: null,
  campaign_id: null,
  ctwa_clid: null,
  language: null,
  opt_in_source: null,
  opt_in_at: null,
  opted_out_at: null,
  service_window_expires_at: null,
  free_entry_window_expires_at: null,
  instagram_scoped_id: null,
  messenger_scoped_id: null,
  facebook_user_id: null,
  qualify_slots: {},
  qualify_state: null,
  score_rationale: null,
  messages: [],
  activity: [],
  reply_options: [{ channel: "whatsapp", free_text: true, templates: true, open_until: "2099-01-01T00:00:00Z", reason: "" }],
  default_channel: "whatsapp",
};

describe("Composer", () => {
  it("sends a message once and clears the box", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ decision: "queued", reason: "", message: null })));
    vi.stubGlobal("fetch", fetchMock);
    wrap(<Composer lead={base} />);
    expect(screen.getByText("Sending a message pauses the assistant for this customer.")).toBeInTheDocument();
    const box = screen.getByLabelText("Your message");
    await userEvent.type(box, "Hi Priya, shall I call you?");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(box).toHaveValue(""));
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/leads/L1/message");
    const body = JSON.parse(init.body);
    expect(body).toMatchObject({ text: "Hi Priya, shall I call you?", channel: "whatsapp" });
    expect(body.client_message_id).toMatch(/^[0-9a-f]+$/);
  });

  it("explains why you can't type and offers a template", async () => {
    const lead = {
      ...base,
      reply_options: [
        { channel: "whatsapp" as const, free_text: false, templates: true, open_until: null, reason: "More than 24 hours since their last WhatsApp message. Send an approved template." },
      ],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ source: "settings", error: null, templates: [{ name: "lead_form_welcome", language: "en", category: "marketing", status: "not checked", body: "Hi {{1}}, about {{2}}", param_count: 2 }] }),
        ),
      ),
    );
    wrap(<Composer lead={lead} />);
    expect(screen.getByText(/More than 24 hours/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Your message")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Send a template" }));
    expect(await screen.findByLabelText("Value for {{1}}")).toHaveValue("Priya");
    expect(screen.getByLabelText("Value for {{2}}")).toHaveValue("Air Fryers");
    // No WhatsApp opt-in: marketing templates would be blocked, so say so up front.
    expect(screen.getByText(/has not agreed to marketing messages/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send template" })).toBeDisabled();
  });

  it("says when there is no way to reach the customer", () => {
    wrap(<Composer lead={{ ...base, reply_options: [] }} />);
    expect(screen.getByText(/no way to message this customer yet/)).toBeInTheDocument();
  });
});

describe("Conversation", () => {
  it("shows who wrote each message", () => {
    const lead: LeadDetail = {
      ...base,
      messages: [
        { message_id: "1", channel: "instagram", direction: "inbound", sender: "lead", text: "price?", media_type: "comment", template_name: null, pricing_category: null, delivery_status: "received", estimated_cost: "0", error: null, timestamp: "2026-10-15T09:00:00Z", kind: "comment" },
        { message_id: "2", channel: "instagram", direction: "outbound", sender: "agent", text: "It is ₹4,999", media_type: "none", template_name: null, pricing_category: "free", delivery_status: "sent", estimated_cost: "0", error: null, timestamp: "2026-10-15T09:00:05Z", kind: "private_reply" },
        { message_id: "3", channel: "whatsapp", direction: "outbound", sender: "salesperson", text: "Calling you now", media_type: "none", template_name: null, pricing_category: "service", delivery_status: "failed", estimated_cost: "0.12", error: "131047: Re-engagement message", timestamp: "2026-10-15T09:10:00Z", kind: "text" },
        { message_id: "4", channel: "whatsapp", direction: "outbound", sender: "agent", text: "[template lead_form_welcome] Priya | Air Fryers", media_type: "none", template_name: "lead_form_welcome", pricing_category: "marketing", delivery_status: "read", estimated_cost: "0.86", error: null, timestamp: "2026-10-15T09:11:00Z", kind: "template" },
      ],
      activity: [{ activity_id: "a", actor: "me@x.com", kind: "takeover", detail: { reason: "manual" }, created_at: "2026-10-15T09:05:00Z" }],
    };
    Element.prototype.scrollIntoView = vi.fn();
    wrap(<Conversation lead={lead} me="me@x.com" />);
    expect(screen.getByText("Commented on a post")).toBeInTheDocument();
    expect(screen.getByText("Private reply to their comment, by the assistant")).toBeInTheDocument();
    expect(screen.getByText("You")).toBeInTheDocument();
    expect(screen.getByText(/Not delivered: 131047/)).toBeInTheDocument();
    expect(screen.getByText("You took over; the assistant paused")).toBeInTheDocument();
    expect(screen.getByText('Template "lead_form_welcome", sent by the assistant')).toBeInTheDocument();
    expect(screen.getByText("Filled in with: Priya, Air Fryers")).toBeInTheDocument();
  });
});

describe("parseAttributes", () => {
  it("keeps numbers and booleans", () => {
    expect(parseAttributes("power watts: 1400\nvoltage_protection: true\ncolour: White\n\nbad line")).toEqual({
      power_watts: 1400,
      voltage_protection: true,
      colour: "White",
    });
  });
});

describe("form controls", () => {
  it("lets the caller set width and height (found in the Week 4 browser run)", () => {
    render(
      <>
        <Select aria-label="narrow" className="h-9 w-auto" />
        <Select aria-label="default" />
      </>,
    );
    const narrow = screen.getByLabelText("narrow").className.split(" ");
    expect(narrow).toContain("w-auto");
    expect(narrow).not.toContain("w-full");
    expect(narrow).not.toContain("h-10");
    const plain = screen.getByLabelText("default").className.split(" ");
    expect(plain).toEqual(expect.arrayContaining(["w-full", "h-10"]));
  });
});
