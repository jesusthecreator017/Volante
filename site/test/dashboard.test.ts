import { afterEach, beforeAll, describe, expect, test, vi } from "vitest";
import { me } from "../src/api";
import { renderAccount } from "../src/components";
import { installMock } from "../src/mock";
import { GROUP_LABEL, dashboard, sessionPage } from "../src/pages/dashboard";
import "../src/routes";
import { match } from "../src/router";
import type { Me } from "../src/types";

const EVIL = "<img src=x onerror=alert(1)>";
const user = { id: 1, login: "kirbyfan5000", avatar_url: "https://avatars.githubusercontent.com/u/1" };

/** The mock API with a recorder (and an optional rewrite of one response). */
function spyFetch(rewrite?: (url: string, body: any) => any) {
  const base = window.fetch;
  const urls: string[] = [];
  vi.stubGlobal("fetch", async (u: string, init?: RequestInit) => {
    urls.push(String(u));
    const r = await base(u, init);
    if (!rewrite || !r.ok) return r;
    return Response.json(rewrite(String(u), await r.json()));
  });
  return urls;
}

beforeAll(async () => {
  await installMock();
});
afterEach(() => {
  vi.unstubAllGlobals();
  sessionStorage.removeItem("oso-mock-unlinked");
});

describe("account menu", () => {
  test("test_menu_shows_dashboard_only_when_linked", () => {
    for (const linked of [true, false]) {
      const el = document.createElement("div");
      renderAccount(el, { user, csrf: "x", uploads_left_today: 5, app_linked: linked } as Me);
      expect(!!el.querySelector('.menu a[href="/account/dashboard"]'), String(linked)).toBe(linked);
      expect(el.querySelector('.menu a[href="/settings"]')).not.toBeNull();
    }
  });
});

describe("stroke groups", () => {
  test("labels follow report-format.md §3", () => {
    expect(GROUP_LABEL.power).toBe("Power (smash, clear)");
    expect(GROUP_LABEL.mid).toBe("Mid (drive, drop, lift, net kill, long serve)");
    expect(GROUP_LABEL.touch).toBe("Touch (net shot, push, short serve)");
  });
});

describe("routes", () => {
  test("dashboard routes take app session ids", () => {
    expect(match("/account/dashboard")?.route.title).toBe("Dashboard");
    expect(match("/account/dashboard/sessions/s01.a-b_c")?.params).toEqual({ id: "s01.a-b_c" });
    expect(match("/account/dashboard/sessions/a b")).toBeNull();
    for (const bad of [".", "..", ".a", "-a", "_a"]) expect(match("/account/dashboard/sessions/" + bad), bad).toBeNull();
    expect(match("/account/dashboard/sessions/" + "x".repeat(64))?.params.id).toHaveLength(64);
    expect(match("/account/dashboard/sessions/" + "x".repeat(65))).toBeNull();
  });
});

describe("dashboard pages", () => {
  test("signed out: both pages show a sign-in link", async () => {
    vi.stubGlobal("fetch", async () => Response.json({ user: null, csrf: "x" }));
    await me(true);
    const a = root();
    await dashboard(a);
    const b = root();
    await sessionPage(b, { id: "s01" });
    expect(a.querySelector('a[href^="/auth/github/login"]')?.getAttribute("href")).toContain("next=");
    expect(b.querySelector('a[href^="/auth/github/login"]')).not.toBeNull();
  });

  test("test_dashboard_unlinked_explainer", async () => {
    sessionStorage.setItem("oso-mock-unlinked", "1");
    const urls = spyFetch();
    await me(true);
    const a = root();
    await dashboard(a);
    const b = root();
    await sessionPage(b, { id: "s01" });
    expect(a.textContent).toContain("Sign in to the Arena in the Volante app to see your stats here");
    expect(b.textContent).toContain("Sign in to the Arena in the Volante app");
    expect(urls.filter((u) => u !== "/api/me")).toEqual([]);
    expect(a.querySelector("svg.dchart")).toBeNull();
  });

  test("test_dashboard_renders_overview", async () => {
    await me(true);
    const el = root();
    await dashboard(el);
    const text = el.textContent ?? "";
    expect(el.querySelector(".totals")?.textContent).toContain("33");
    expect(el.querySelectorAll(".weekly .dbar").length).toBe(12);
    const bests = el.querySelectorAll('.bests a[href^="/account/dashboard/sessions/"]');
    expect(bests.length).toBe(4);
    expect(text).toContain("estimated");
    expect(text).toContain("experimental");
    expect(el.querySelectorAll(".sessions li").length).toBe(20);
    for (const svg of el.querySelectorAll("svg[role=img]")) expect(svg.getAttribute("aria-label")).toBeTruthy();
    expect(el.querySelectorAll("[style], script").length).toBe(0);
    // pagination: the next page is appended
    (el.querySelector(".more-sessions") as HTMLButtonElement).click();
    await vi.waitFor(() => expect(el.querySelectorAll(".sessions li").length).toBe(33), { timeout: 3000 });
  });

  test("test_session_page_renders_report", async () => {
    await me(true);
    const full = root(); // s01: pod, strokes and heart rate
    await sessionPage(full, { id: "s01" });
    expect(full.querySelectorAll(".timeline .dbar").length).toBeGreaterThan(10);
    expect(full.querySelector(".timeline svg.lines")).not.toBeNull();
    expect(full.querySelector(".strokes svg.mix")).not.toBeNull();
    expect(full.querySelector(".heart")).not.toBeNull();
    expect(full.textContent).toContain("experimental");
    expect(full.textContent).toContain("estimated");
    expect(full.querySelectorAll(".insight").length).toBeGreaterThan(0);
    const bare = root(); // s02: no strokes, no heart rate
    await sessionPage(bare, { id: "s02" });
    expect(bare.querySelector(".strokes svg.mix")).toBeNull();
    expect(bare.querySelector(".heart")).toBeNull();
    expect(bare.querySelector(".timeline .dbar")).not.toBeNull();
    const flat = root(); // s06: an older app build sent no report: the flat numbers
    await sessionPage(flat, { id: "s06" });
    expect(flat.textContent).toContain("No detailed report for this session.");
    expect(flat.textContent).toContain("hits");
    expect(full.textContent).toContain("racquet + watch"); // s01 is pod + watch
    const missing = root();
    await sessionPage(missing, { id: "nope" });
    expect(missing.querySelector(".error-box")).toBeNull();
    expect(missing.textContent).toContain("Session not found — it may have been deleted from the app.");
  });

  test("test_wrist_sessions_labelled", async () => {
    await me(true);
    const el = root();
    await dashboard(el);
    const rows = [...el.querySelectorAll(".sessions li")];
    const wrist = rows.filter((li) => li.textContent?.includes("wrist"));
    expect(wrist.length).toBeGreaterThan(0);
    expect(wrist.length).toBeLessThan(rows.length);
    const page = root(); // s31-s33 are watch sessions
    await sessionPage(page, { id: "s31" });
    expect(page.textContent).toContain("wrist");
    expect(page.textContent).toContain("hand speed");
  });

  test("test_text_nodes_only", async () => {
    spyFetch((u, body) => (u.endsWith("/api/me/sessions/s01") ? (body.report.insights[0].title = EVIL, body) : body));
    await me(true);
    const el = root();
    await sessionPage(el, { id: "s01" });
    expect(el.querySelector("img")).toBeNull();
    expect(el.textContent).toContain(EVIL);
  });
});

function root() {
  return document.createElement("main");
}
