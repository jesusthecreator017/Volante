import { afterEach, describe, expect, test, vi } from "vitest";
import { arenaWatch, landing } from "../src/pages/landing";
import { showLatest } from "../src/pages/model";
import { mountViewer, viewerSrc } from "../src/viewer";

const m = { id: 7, a: { name: "A" }, b: { name: "B" } };
const done = (id: number) => ({ id, status: "done", a: { name: "A" }, b: { name: "B" }, games_won: [2, 0] });
const page = (items: unknown[]) => vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ items, next_before: null }), { headers: { "content-type": "application/json" } })));
const tick = () => new Promise((r) => setTimeout(r, 0));

afterEach(() => vi.unstubAllGlobals());

describe("mountViewer", () => {
  test("auto mounts the iframe at the replay URL", () => {
    const box = document.createElement("div");
    mountViewer(box, m, false);
    expect(box.querySelector("iframe")?.getAttribute("src")).toBe(viewerSrc(7));
    expect(viewerSrc(7)).toBe("/viewer/index.html?match=%2Fapi%2Fmatches%2F7%2Freplay.bin&theme=system");
    document.documentElement.setAttribute("data-theme", "dark");
    expect(viewerSrc(7)).toBe("/viewer/index.html?match=%2Fapi%2Fmatches%2F7%2Freplay.bin&theme=dark");
    document.documentElement.removeAttribute("data-theme");
    expect(box.querySelector("button")).toBeNull();
  });
  test("load-on-tap waits for the button; seek mounts it", () => {
    const box = document.createElement("div");
    const v = mountViewer(box, m, true);
    expect(box.querySelector("iframe")).toBeNull();
    box.querySelector("button")!.click();
    expect(box.querySelectorAll("iframe").length).toBe(1);
    const box2 = document.createElement("div");
    mountViewer(box2, m, true).seek(3);
    expect(box2.querySelector("iframe")).not.toBeNull();
    expect(v.mount()).toBe(box.querySelector("iframe"));
  });
});

describe("model page viewer", () => {
  test("shows the latest finished match with an Open match link", async () => {
    page([{ ...done(9), status: "running" }, done(8)]);
    const slot = document.createElement("section");
    await showLatest(slot, 1);
    expect(slot.querySelector('a[href="/arena/matches/8"]')?.textContent).toBe("Open match");
    expect(slot.querySelector(".viewer")).not.toBeNull();
  });
  test("nothing without a finished match or when the lookup fails", async () => {
    page([{ ...done(9), status: "queued" }]);
    const slot = document.createElement("section");
    await showLatest(slot, 1);
    expect(slot.childElementCount).toBe(0);
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ error: "x" }, { status: 500 })));
    await showLatest(slot, 1);
    expect(slot.childElementCount).toBe(0);
  });
});

describe("landing", () => {
  test("Arena teaser: poster, then a play button that loads the viewer in place", async () => {
    page([done(8)]);
    const el = arenaWatch();
    await tick();
    expect(el.querySelector("img")?.getAttribute("width")).toBe("1280");
    expect(el.querySelector("iframe")).toBeNull();
    el.querySelector("button")!.click();
    expect(el.querySelector("iframe")?.getAttribute("src")).toBe(viewerSrc(8));
  });
  test("Arena teaser with no matches keeps the poster linking to /arena", async () => {
    page([]);
    const el = arenaWatch();
    await tick();
    expect(el.querySelector("button")).toBeNull();
    expect(el.querySelector('a[href="/arena"] img')).not.toBeNull();
  });
  test("no camera replay or live view in the copy", () => {
    page([]);
    const root = document.createElement("div");
    landing(root);
    expect(root.textContent).not.toMatch(/3D replay|Live racquet|stream live|replays/i);
  });
});
