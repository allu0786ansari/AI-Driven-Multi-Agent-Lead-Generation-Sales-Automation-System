import { createHmac } from "node:crypto";
import { expect, test, type Page } from "@playwright/test";

// Needs a backend loaded with `python -m scripts.simulate demo` (see playwright.config.ts).
const EMAIL = process.env.E2E_EMAIL ?? "owner@store.test";
const PASSWORD = process.env.E2E_PASSWORD ?? "sandbox-pass-123";
const SECRET = process.env.E2E_META_APP_SECRET ?? "sandbox-secret";
const SHOTS = "e2e/screenshots";

test.describe.configure({ mode: "serial" });

async function signIn(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/leads/);
}

/** A WhatsApp message signed like Meta signs it. */
async function customerWrites(page: Page, phone: string, name: string, text: string) {
  const body = JSON.stringify({
    object: "whatsapp_business_account",
    entry: [
      {
        id: "200000000000001",
        changes: [
          {
            field: "messages",
            value: {
              messaging_product: "whatsapp",
              metadata: { display_phone_number: "919000000001", phone_number_id: "300000000000001" },
              contacts: [{ profile: { name }, wa_id: phone }],
              messages: [{ from: phone, id: `wamid.E2E${Date.now()}`, timestamp: String(Math.floor(Date.now() / 1000)), type: "text", text: { body: text } }],
            },
          },
        ],
      },
    ],
  });
  const signature = "sha256=" + createHmac("sha256", SECRET).update(body).digest("hex");
  const resp = await page.request.post("/webhooks/whatsapp", {
    data: body,
    headers: { "Content-Type": "application/json", "X-Hub-Signature-256": signature },
  });
  expect(resp.ok()).toBeTruthy();
}

test("sign-in rejects a wrong password with a clear message", async ({ page }) => {
  await page.goto("/leads");
  await expect(page).toHaveURL(/\/login/); // signed out: sent to sign-in
  await page.screenshot({ path: `${SHOTS}/01-login.png` });
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill("wrong-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert")).toHaveText("Incorrect email or password");
});

test("inbox shows who needs a person first", async ({ page }) => {
  await signIn(page);
  const list = page.getByRole("region", { name: "Leads" });
  await expect(list.getByRole("tab", { name: /Needs you/ })).toHaveAttribute("aria-selected", "true");
  await expect(list.getByRole("link", { name: /meena\.cooks/ })).toBeVisible();
  await expect(list.getByRole("link", { name: /Lakshmi R/ })).toBeVisible();
  await expect(list.getByRole("link", { name: /Rahul Sharma/ })).toBeVisible();
  await expect(list.getByRole("link", { name: /Vikram Nair/ })).toHaveCount(0); // assistant is handling him
  await list.getByRole("tab", { name: /Open/ }).click();
  await expect(list.getByRole("link", { name: /Vikram Nair/ })).toBeVisible();
  await expect(list.getByRole("link", { name: /promo_deals/ })).toHaveCount(0); // spam never becomes a lead
  await page.screenshot({ path: `${SHOTS}/02-inbox-open.png` });
  await list.getByLabel("Search leads").fill("arjun");
  await expect(list.getByRole("link")).toHaveCount(1);
});

test("a hot lead: read, take over, reply, hand back, mark as bought", async ({ page }) => {
  await signIn(page);
  await page.getByRole("link", { name: /meena\.cooks/ }).click();
  const convo = page.getByRole("region", { name: "Conversation" });
  await expect(convo.getByText("Commented on a post")).toBeVisible();
  await expect(convo.getByText("Private reply to their comment, by the assistant")).toBeVisible();
  const details = page.getByRole("complementary", { name: "Lead details" });
  await expect(details.getByLabel(/Score \d+, Hot/)).toBeVisible();
  await expect(details.getByText("Coimbatore", { exact: true })).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/03-lead-hot.png` });
  await details.getByRole("button", { name: "Yes", exact: true }).click(); // "Is this lead really hot?"
  await expect(details.getByText("You confirmed this lead is hot.")).toBeVisible();

  await page.getByRole("button", { name: "Take over" }).click();
  await expect(page.getByText("You're handling this conversation.", { exact: false }).first()).toBeVisible();
  await page.getByLabel("Your message").fill("Hi Meena, this is Ravi from the store. Both air fryers are ready - shall I book delivery for Thursday?");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(convo.getByText("shall I book delivery for Thursday?", { exact: false })).toBeVisible();
  await expect(convo.getByText("Sent").last()).toBeVisible({ timeout: 15000 }); // the worker sent it
  await expect(page.getByLabel("Your message")).toHaveValue("");
  await page.screenshot({ path: `${SHOTS}/04-lead-replied.png` });

  await page.getByRole("button", { name: "Hand back to assistant" }).click();
  await expect(convo.getByText("You handed it back to the assistant")).toBeVisible();
  await page.getByLabel("Mark lead as").selectOption("converted");
  await expect(convo.getByText("You marked this lead as Bought")).toBeVisible();
  const list = page.getByRole("region", { name: "Leads" });
  await list.getByRole("tab", { name: /Closed/ }).click();
  await expect(list.getByRole("link", { name: /meena\.cooks/ })).toBeVisible();
});

test("a new handoff appears live, without reloading", async ({ page }) => {
  await signIn(page);
  await expect(page.getByText("Live", { exact: true })).toBeVisible();
  const phone = `9198${String(Date.now()).slice(-8)}`;
  await customerWrites(page, phone, "Kavya Iyer", "Can someone call me please? I want to order for my shop");
  const toast = page.getByRole("link", { name: /Needs a person: Kavya Iyer/ });
  await expect(toast).toBeVisible({ timeout: 15000 });
  await expect(page.getByRole("region", { name: "Leads" }).getByRole("link", { name: /Kavya Iyer/ })).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/05-live-alert.png` });
  await toast.click();
  await expect(
    page.getByRole("region", { name: "Conversation" }).getByText("Sure! A member of our team will contact you shortly."),
  ).toBeVisible();
  await page.getByRole("link", { name: "Alerts" }).click();
  await expect(page.getByRole("heading", { name: "Alerts" })).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/06-alerts.png` });
});

test("products: add one and check the assistant finds it", async ({ page }) => {
  await signIn(page);
  await page.getByRole("link", { name: "Products" }).click();
  await page.getByRole("button", { name: "Add product" }).click();
  const form = page.getByRole("complementary", { name: "Edit product" });
  const sku = `TO-${Date.now() % 100000}`;
  await form.getByLabel("Name").fill("CrispToast 2-Slice Toaster");
  await form.getByLabel("Product code (SKU)").fill(sku);
  await form.getByLabel("Category").fill("Toasters");
  await form.getByLabel("Price (₹)").fill("1499");
  await form.getByLabel("Description").fill("Pop-up toaster with 7 browning levels.");
  await form.getByLabel("Details").fill("warranty_years: 1\nslots: 2");
  await form.getByRole("button", { name: "Add product" }).click();
  await expect(page.getByRole("cell", { name: /CrispToast 2-Slice Toaster/ })).toBeVisible();
  await page.getByLabel("Customer message").fill("toaster kitne ka hai");
  await page.getByRole("button", { name: "Check" }).click();
  await expect(page.locator("ol li").first()).toContainText("CrispToast 2-Slice Toaster");
  await page.screenshot({ path: `${SHOTS}/07-products.png`, fullPage: true });
});

test("settings: change when a lead counts as hot", async ({ page }) => {
  await signIn(page);
  await page.getByRole("link", { name: "Settings" }).click();
  await page.getByLabel("Hot from").fill("65");
  await page.getByRole("button", { name: "Save scoring and questions" }).click();
  await expect(page.getByText("Saved. New scores apply", { exact: false })).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("Hot from")).toHaveValue("65");
  await page.getByLabel("Hot from").fill("70");
  await page.getByRole("button", { name: "Save scoring and questions" }).click();
  await page.screenshot({ path: `${SHOTS}/08-settings.png` });
  await page.getByRole("link", { name: "Activity" }).click();
  await expect(page.getByRole("cell", { name: "Instagram comment" }).first()).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/09-activity.png` });
});

test("follow-ups: a quiet customer who agreed gets one, shown in the conversation", async ({ page }) => {
  await signIn(page);
  await page.getByRole("link", { name: "Settings" }).click();
  const planned = page.getByRole("heading", { name: "Planned next" }).locator("../..");
  await expect(planned.getByText("Arjun Mehta")).toBeVisible(); // said "Haan" to the opt-in question
  // Make the first follow-up due after ~9 seconds, at any hour, then check now.
  await page.getByLabel("Follow-up 1: days without a reply").fill("0.0001");
  await page.getByLabel("Send from (hour)").fill("0");
  await page.getByLabel("Until (hour)").fill("24");
  await page.getByRole("button", { name: "Save follow-ups" }).click();
  await expect(page.getByText("Saved. Every lead's next follow-up", { exact: false })).toBeVisible();
  await page.waitForTimeout(9000);
  await page.getByRole("button", { name: "Check now" }).click();
  await expect(page.getByText(/follow-ups? sent\./)).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/10-settings-follow-ups.png`, fullPage: true });
  // back to the usual rules
  await page.getByLabel("Follow-up 1: days without a reply").fill("2");
  await page.getByLabel("Send from (hour)").fill("10");
  await page.getByLabel("Until (hour)").fill("19");
  await page.getByRole("button", { name: "Save follow-ups" }).click();
  await expect(page.getByRole("button", { name: "Save follow-ups" })).toBeDisabled();

  await page.getByRole("link", { name: "Leads" }).click();
  const list = page.getByRole("region", { name: "Leads" });
  await list.getByRole("tab", { name: /All/ }).click();
  await list.getByRole("link", { name: /Arjun Mehta/ }).click();
  const convo = page.getByRole("region", { name: "Conversation" });
  await expect(convo.getByText("Follow-up after a quiet spell, sent automatically")).toBeVisible();
  await expect(convo.getByText(/Hi Arjun, just checking in about/)).toBeVisible(); // the template as the customer saw it
  await expect(convo.getByText("Can we send you offers and reminders on WhatsApp? Reply YES or NO.", { exact: false })).toBeVisible();
  const details = page.getByRole("complementary", { name: "Lead details" });
  await expect(details.getByText(/Agreed \(said yes in the chat/)).toBeVisible();
  await expect(details.getByText(/1 sent so far/)).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/11-lead-follow-up.png` });
});

test("analytics: the period's numbers, with a chart you can read by keyboard", async ({ page }) => {
  await signIn(page);
  await page.getByRole("link", { name: "Analytics" }).click();
  await expect(page.getByRole("heading", { name: "Analytics" })).toBeVisible();
  const chart = page.getByRole("img", { name: /New leads per day/ });
  await expect(chart).toBeVisible();
  await chart.focus();
  await page.keyboard.press("End");
  await page.keyboard.press("ArrowLeft");
  await expect(chart.getByText(/\d+ leads?/)).toBeVisible(); // tooltip for the focused day
  await expect(page.getByText("Follow-ups sent")).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/12-analytics.png`, fullPage: true });
  await page.getByRole("radio", { name: "7 days" }).click();
  await expect(page.getByRole("radio", { name: "7 days" })).toHaveAttribute("aria-checked", "true");
  await expect(page.getByText("Last 7 days, Asia/Kolkata time.")).toBeVisible();
});

test("erase a customer's personal data on request", async ({ page }) => {
  await signIn(page);
  const list = page.getByRole("region", { name: "Leads" });
  await list.getByRole("tab", { name: /All/ }).click();
  await list.getByRole("link", { name: /Vikram Nair/ }).click();
  const details = page.getByRole("complementary", { name: "Lead details" });
  await details.getByRole("button", { name: "Erase personal data" }).click();
  await details.getByRole("button", { name: "Erase for good" }).click();
  await expect(page.getByRole("heading", { name: "Erased customer" })).toBeVisible();
  await expect(details.getByText(/Erased on/)).toBeVisible();
  await expect(page.getByRole("region", { name: "Conversation" }).getByText("[erased]").first()).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/13-erased.png` });
});

test.describe("phone", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });

  test("inbox, conversation and details work on a phone", async ({ page }) => {
    await signIn(page);
    await page.screenshot({ path: `${SHOTS}/14-phone-inbox.png` });
    await page.getByRole("link", { name: /Lakshmi R/ }).click();
    await expect(page.getByRole("region", { name: "Leads" })).toBeHidden();
    const convo = page.getByRole("region", { name: "Conversation" });
    await expect(convo.getByText("Thanks for the voice note!", { exact: false })).toBeVisible();
    await page.screenshot({ path: `${SHOTS}/15-phone-lead.png` });
    await page.getByRole("button", { name: "Details" }).click();
    await expect(page.getByRole("complementary", { name: "Lead details" })).toBeVisible();
    await page.screenshot({ path: `${SHOTS}/16-phone-details.png` });
    await page.getByRole("button", { name: "Close" }).click();
    await page.getByRole("link", { name: "Back to leads" }).click();
    await expect(page.getByRole("region", { name: "Leads" })).toBeVisible();
    const width = await page.evaluate(() => document.documentElement.scrollWidth);
    expect(width).toBeLessThanOrEqual(390); // no sideways scrolling
    await page.getByRole("link", { name: "Analytics" }).click();
    await expect(page.getByRole("img", { name: /New leads per day/ })).toBeVisible();
    await page.screenshot({ path: `${SHOTS}/17-phone-analytics.png`, fullPage: true });
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  });
});
