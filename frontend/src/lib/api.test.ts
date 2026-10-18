import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, errorText, getToken, setToken, setUnauthorizedHandler } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
  setToken(null);
});

describe("errorText", () => {
  it("reads FastAPI messages", () => {
    expect(errorText({ detail: "Lead not found" }, "x")).toBe("Lead not found");
    expect(errorText({ detail: [{ loc: ["body", "price"], msg: "Input should be greater than 0" }] }, "x")).toBe(
      "price: Input should be greater than 0",
    );
    expect(errorText({ detail: [{ loc: ["body"], msg: "Value error, Send either text or a template" }] }, "x")).toBe(
      "Send either text or a template",
    );
    expect(errorText(null, "fallback")).toBe("fallback");
  });
});

describe("api", () => {
  it("sends the token and JSON body", async () => {
    setToken("T1");
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(api("/api/x", { method: "POST", json: { a: 1 } })).resolves.toEqual({ ok: true });
    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers.get("Authorization")).toBe("Bearer T1");
    expect(init.headers.get("Content-Type")).toBe("application/json");
    expect(init.body).toBe('{"a":1}');
  });

  it("signs out on 401", async () => {
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 401 })));
    await expect(api("/api/x")).rejects.toMatchObject({ status: 401 });
    expect(handler).toHaveBeenCalledOnce();
  });

  it("turns errors into readable messages", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "A product with SKU X already exists" }), { status: 409 })));
    const err = (await api("/api/x").catch((e: unknown) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.message).toBe("A product with SKU X already exists");
  });

  it("explains when the server is down", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(api("/api/x")).rejects.toThrow("Can't reach the server");
  });

  it("keeps the token in memory when storage is blocked", () => {
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    const get = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    setToken("MEM");
    expect(getToken()).toBe("MEM");
    spy.mockRestore();
    get.mockRestore();
  });
});
