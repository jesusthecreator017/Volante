import { afterEach, describe, expect, test, vi } from "vitest";
import { ApiError, api, me } from "../src/api";
import { PLAY_STYLES, checkUploadFile, suggestStyle } from "../src/arena";
import { buildBars, ratingChart } from "../src/components";
import { h } from "../src/dom";
import { getStarted } from "../src/pages/content";
import { FEATURES, SESSION, exampleSwings, landing } from "../src/pages/landing";
import "../src/routes";
import { match, redirectFor, route } from "../src/router";

describe("arena rules", () => {
  test("every preset sums to 24 and suggests itself", () => {
    for (const p of PLAY_STYLES) {
      expect(p.build.power + p.build.speed + p.build.reach + p.build.accuracy).toBe(24);
      expect(suggestStyle(p.build)).toBe(p.name);
    }
  });
  test("nearest preset for an off-preset build", () => {
    expect(suggestStyle({ power: 10, speed: 6, reach: 4, accuracy: 4 })).toBe("Smasher");
    expect(suggestStyle({ power: 3, speed: 7, reach: 8, accuracy: 6 })).toBe("Wall");
  });
  test("upload pre-checks: extension and size only", () => {
    expect(checkUploadFile({ name: "m.safetensors", size: 300_000 })).toBeNull();
    expect(checkUploadFile({ name: "M.SAFETENSORS", size: 1_048_576 })).toBeNull();
    expect(checkUploadFile({ name: "m.safetensors", size: 1_048_577 })).toMatch(/1 MiB/);
    expect(checkUploadFile({ name: "m.pt", size: 10 })).toMatch(/safetensors/);
    expect(checkUploadFile({ name: "m.safetensors", size: 0 })).toMatch(/empty/);
  });
});

describe("DOM safety", () => {
  test("strings are text, never markup", () => {
    const el = h("p", null, "<img src=x onerror=alert(1)>", h("b", null, "<script>1</script>"));
    expect(el.querySelector("img")).toBeNull();
    expect(el.querySelector("script")).toBeNull();
    expect(el.textContent).toBe("<img src=x onerror=alert(1)><script>1</script>");
  });
  test("style attributes are refused (strict CSP)", () => {
    expect(() => h("div", { style: "color:red" })).toThrow(/CSP/);
  });
  test("charts and build bars carry no inline styles", () => {
    const bars = buildBars({ power: 9, speed: 6, reach: 5, accuracy: 4 });
    const chart = ratingChart([{ t: "2026-10-01T00:00:00Z", rating: 1200 }, { t: "2026-10-02T00:00:00Z", rating: 1250 }]);
    for (const el of [bars, chart]) expect(el.querySelectorAll("[style]").length).toBe(0);
    expect(bars.querySelector(".v9")).not.toBeNull();
  });
});

describe("get started copy", () => {
  test("tells the truth about installing the kit and the coach skill", () => {
    const root = document.createElement("div");
    getStarted(root);
    const text = root.textContent ?? "";
    expect(text).not.toMatch(/pip install oso-arena(\s|$)/); // the name is not registered on PyPI
    expect(text).toContain('pip install "./arena/kit[train]"');
    expect(text).toContain("oso-arena train");
    expect(text).toContain("skills/oso-arena-coach");
    expect(text).toContain("not bundled in this version of the kit");
    expect(root.querySelectorAll("[style], script").length).toBe(0);
  });
});

describe("router", () => {
  test("patterns with numeric params", () => {
    const page = () => {};
    route("/zz/:id", "Z", page);
    expect(match("/zz/42")?.params).toEqual({ id: "42" });
    expect(match("/zz/42/")?.params).toEqual({ id: "42" });
    expect(match("/zz/abc")).toBeNull();
  });
});

describe("routes", () => {
  test("every page route resolves, Arena pages live under /arena", () => {
    const titles: Record<string, string> = {
      "/": "Racquet pod",
      "/settings": "Settings",
      "/terms": "Terms",
      "/privacy": "Privacy",
      "/arena": "Arena",
      "/arena/ladder": "Ladder · Arena",
      "/arena/models/7": "Model · Arena",
      "/arena/matches/905": "Match · Arena",
      "/arena/tournaments": "Tournaments · Arena",
      "/arena/tournaments/3": "Tournament · Arena",
      "/arena/my-models": "My models · Arena",
      "/arena/get-started": "Get started · Arena",
      "/arena/": "Arena",
    };
    for (const [path, title] of Object.entries(titles)) expect(match(path)?.route.title, path).toBe(title);
    expect(match("/arena/models/7")?.params).toEqual({ id: "7" });
  });
  test("old Arena paths are no longer routes and redirect to /arena/*", () => {
    const old: Record<string, string> = {
      "/ladder": "/arena/ladder",
      "/models/12": "/arena/models/12",
      "/matches/905": "/arena/matches/905",
      "/tournaments": "/arena/tournaments",
      "/tournaments/3": "/arena/tournaments/3",
      "/my-models": "/arena/my-models",
      "/get-started": "/arena/get-started",
      "/ladder/": "/arena/ladder",
    };
    for (const [from, to] of Object.entries(old)) {
      expect(match(from), from).toBeNull();
      expect(redirectFor(from), from).toBe(to);
      expect(match(to), to).not.toBeNull();
    }
    for (const ok of ["/", "/arena", "/arena/ladder", "/settings", "/terms", "/privacy", "/nope"]) expect(redirectFor(ok), ok).toBeNull();
  });
  test("unknown paths are 404", () => {
    for (const p of ["/nope", "/arena/nope", "/arena/models/abc", "/settings/x"]) expect(match(p), p).toBeNull();
  });
});

describe("landing", () => {
  afterEach(() => vi.unstubAllGlobals());
  test("statuses are honest and the page has no inline styles", () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ items: [], next_before: null })));
    const status = Object.fromEntries(FEATURES.map((f) => [f.id, f.status]));
    expect(status).toEqual({ hits: "Available", speed: "Available", strokes: "In development", insights: "In development" });
    const root = document.createElement("div");
    landing(root);
    expect(root.querySelectorAll("[style], script").length).toBe(0);
    expect(root.querySelectorAll("img:not([alt]), img:not([width]), img:not([height])").length).toBe(0);
    expect(root.querySelector('a[href="/#how"]')).not.toBeNull();
    expect(root.querySelector('a[href="/arena"]')).not.toBeNull();
    expect(root.textContent).toContain("Example");
  });
  test("example series matches the real session's mean and max", () => {
    const v = exampleSwings();
    expect(v.length).toBe(SESSION.smashes);
    expect(Math.max(...v)).toBeCloseTo(SESSION.maxKmh, 6);
    expect(v.reduce((a, b) => a + b, 0) / v.length).toBeCloseTo(SESSION.meanKmh, 6);
  });
});

describe("API client", () => {
  afterEach(() => vi.unstubAllGlobals());
  const respond = (status: number, body: unknown, headers: Record<string, string> = {}) =>
    new Response(body === null ? null : JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } });

  test("CSRF token from /api/me goes on state-changing requests, same-origin credentials", async () => {
    const calls: { url: string; init: RequestInit }[] = [];
    vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      if (url === "/api/me") return respond(200, { user: null, csrf: "tok-1" });
      return respond(204, null);
    });
    await me(true);
    await api.retireModel(7);
    const del = calls.find((c) => c.url === "/api/models/7")!;
    expect(del.init.method).toBe("DELETE");
    expect((del.init.headers as Record<string, string>)["x-csrf-token"]).toBe("tok-1");
    expect(del.init.credentials).toBe("same-origin");
    expect(calls.find((c) => c.url === "/api/me")!.init.headers).not.toHaveProperty("x-csrf-token");
  });

  test("errors carry code, detail and Retry-After", async () => {
    vi.stubGlobal("fetch", async () => respond(429, { error: "rate_limited", detail: "Daily limit." }, { "retry-after": "120" }));
    const e = await api.seasons().catch((x) => x);
    expect(e).toBeInstanceOf(ApiError);
    expect([e.status, e.code, e.detail, e.retryAfter]).toEqual([429, "rate_limited", "Daily limit.", 120]);
  });

  test("non-JSON error pages get a plain message", async () => {
    vi.stubGlobal("fetch", async () => new Response("<html>bad gateway</html>", { status: 502 }));
    const e = await api.seasons().catch((x) => x);
    expect(e.status).toBe(502);
    expect(e.detail).toMatch(/502/);
  });

  test("query strings skip empty params", async () => {
    const urls: string[] = [];
    vi.stubGlobal("fetch", async (u: string) => (urls.push(u), respond(200, { items: [], next_before: null })));
    await api.matches({ model: 3 });
    await api.ladder(undefined, 50);
    expect(urls).toEqual(["/api/matches?model=3&limit=20", "/api/ladder?offset=50&limit=50"]);
  });
});
