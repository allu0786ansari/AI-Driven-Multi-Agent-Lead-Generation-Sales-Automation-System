import { describe, expect, it } from "vitest";
import { formatCost, formatINR, heatOf, rationaleLines, shortTime, slotFacts, windowLeft } from "./format";

describe("formatINR", () => {
  it.each([
    [1199, "₹1,199"],
    ["112999", "₹1,12,999"],
    ["999.5", "₹999.50"],
    ["12999.00", "₹12,999"],
    [75, "₹75"],
    [10000000, "₹1,00,00,000"],
  ])("%s -> %s", (input, out) => expect(formatINR(input)).toBe(out));

  it("handles empty values", () => {
    expect(formatINR(null)).toBe("");
    expect(formatINR("")).toBe("");
  });
});

describe("heatOf", () => {
  it("uses the status when it is a temperature", () => {
    expect(heatOf("hot", 10)).toBe("hot");
    expect(heatOf("cold", 90)).toBe("cold");
  });
  it("falls back to the score for salesperson statuses", () => {
    expect(heatOf("contacted", 75)).toBe("hot");
    expect(heatOf("contacted", 45)).toBe("warm");
    expect(heatOf("new", null)).toBe("none");
  });
});

describe("times", () => {
  const now = new Date("2026-10-15T12:00:00");
  it("short time", () => {
    expect(shortTime("2026-10-15T11:59:30", now)).toBe("now");
    expect(shortTime("2026-10-15T11:40:00", now)).toBe("20 min");
    expect(shortTime("2026-10-15T08:00:00", now)).toBe("4 h");
    expect(shortTime("2026-10-14T20:00:00", now)).toBe("Yesterday");
    expect(shortTime(null, now)).toBe("");
  });
  it("window left", () => {
    expect(windowLeft("2026-10-15T15:20:00", now)).toBe("Closes in 3 h 20 min");
    expect(windowLeft("2026-10-15T12:45:00", now)).toBe("Closes in 45 min");
    expect(windowLeft("2026-10-15T11:00:00", now)).toBe("Closed");
  });
});

describe("lead facts", () => {
  it("lists what the customer told us in reading order", () => {
    expect(
      slotFacts({ product: "CrispAir 6L", quantity: 2, budget: "15000", timeline: "this_week", city: "Chennai", purchase_intent: "high" }),
    ).toEqual([
      { label: "Wants", value: "CrispAir 6L" },
      { label: "Quantity", value: "2" },
      { label: "Budget", value: "₹15,000" },
      { label: "Buying", value: "This week" },
      { label: "Delivery", value: "Chennai" },
      { label: "Intent", value: "Ready to buy" },
    ]);
    expect(slotFacts({})).toEqual([]);
  });

  it("splits the score rationale into lines with points", () => {
    expect(rationaleLines("wants Kettle (+20); budget ₹1,199 (+10); unhappy (-10)")).toEqual([
      { text: "wants Kettle", points: 20 },
      { text: "budget ₹1,199", points: 10 },
      { text: "unhappy", points: -10 },
    ]);
    expect(rationaleLines("not enough information yet")).toEqual([]);
    expect(rationaleLines(null)).toEqual([]);
  });

  it("cost keeps paise", () => {
    expect(formatCost("0.8631")).toBe("₹0.86");
    expect(formatCost("0")).toBe("₹0");
  });
});

describe("found in the Week 4 browser run", async () => {
  const { displayName, handlerText, previewText } = await import("./format");
  it("names DM customers by channel instead of 'Unnamed customer'", () => {
    expect(displayName({ name: null, contact_phone: null, preferred_channel: "instagram" })).toBe("Instagram customer");
    expect(displayName({ name: null, contact_phone: "+9198", preferred_channel: "whatsapp" })).toBe("+9198");
  });
  it("does not say 'You're handling this' when the assistant handed the lead over", () => {
    expect(handlerText({ is_human_takeover: true, assigned_to: null }, "me@x")).toBe("Waiting for a person");
    expect(handlerText({ is_human_takeover: true, assigned_to: "me@x" }, "me@x")).toBe("You're handling this");
    expect(handlerText({ is_human_takeover: true, assigned_to: "Ravi" }, "me@x")).toBe("Ravi is handling this");
    expect(handlerText({ is_human_takeover: false, assigned_to: "Ravi" }, "me@x")).toBeNull();
  });
  it("shows a catalog reply on one line", () => {
    expect(previewText("Here are the options that match:\n• A – ₹1\n• B – ₹2\nWhich one?")).toBe("Options: A – ₹1, B – ₹2, Which one?");
  });
});

describe("conversation order", async () => {
  const { byTime } = await import("./format");
  it("puts a whole-second message before a reply later in the same second", () => {
    const items = [
      { at: "2026-10-15T01:57:56.889691Z", id: "reply" },
      { at: "2026-10-15T01:57:56Z", id: "voice note" },
    ];
    expect(items.sort(byTime).map((i) => i.id)).toEqual(["voice note", "reply"]);
  });
});

describe("Week 5 helpers", async () => {
  const { fillTemplate, duration, pct, consentText, displayName } = await import("./format");
  const { niceMax } = await import("../components/charts");
  it("fills a template the way WhatsApp shows it", () => {
    expect(fillTemplate("Hi {{1}}, about {{2}}: {{ 3 }}", ["Priya", "Air Fryers"])).toBe("Hi Priya, about Air Fryers: {{ 3 }}");
  });
  it("durations and percentages", () => {
    expect(duration(3.4)).toBe("3 s");
    expect(duration(0.2)).toBe("under 1 s");
    expect(duration(65)).toBe("1 min 5 s");
    expect(duration(7800)).toBe("2 h 10 min");
    expect(duration(null)).toBe("-");
    expect(pct(66.66)).toBe("66.7%");
    expect(pct(50)).toBe("50%");
    expect(pct(null)).toBe("-");
  });
  it("shows a template in the inbox by name, not as its raw values", async () => {
    const { previewText } = await import("./format");
    expect(previewText("[template nurture_followup_1] Arjun | PowerChef")).toBe("Check-in template");
    expect(previewText("[template my_promo] x")).toBe("my_promo template");
  });
  it("chart axis rounds up to a clean number", () => {
    expect([0, 3, 7, 12, 26, 180].map(niceMax)).toEqual([1, 5, 10, 20, 50, 200]);
  });
  it("says what the customer agreed to", () => {
    const base = { opted_out: false, opted_out_at: null, whatsapp_opt_in: false, opt_in_source: null, opt_in_at: null, opt_in_asked_at: null, contact_phone: "+91" };
    expect(consentText(base)).toBe("Not asked yet");
    expect(consentText({ ...base, opt_in_asked_at: "2026-10-18T10:00:00Z" })).toBe("Asked in the chat, no yes yet");
    expect(consentText({ ...base, whatsapp_opt_in: true, opt_in_source: "ctwa_message", opt_in_at: "2026-10-18T10:00:00Z" })).toMatch(/^Agreed \(said yes in the chat, 18 Oct\)$/);
    expect(consentText({ ...base, opted_out: true, opted_out_at: "2026-10-18T10:00:00Z" })).toMatch(/^Opted out on 18 Oct/);
    expect(displayName({ name: "Ravi", contact_phone: null, erased_at: "2026-10-18T10:00:00Z" })).toBe("Erased customer");
  });
});
