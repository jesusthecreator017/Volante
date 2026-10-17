import { afterEach, beforeAll, describe, expect, test, vi } from "vitest";
import { api, me } from "../src/api";
import { installMock } from "../src/mock";
import { checkPodName, renameForm } from "../src/pods";
import type { Pod } from "../src/types";

const pod: Pod = { id: "pod_1", hw_id: "OSO-4C99", name: "Old", last_seen: null };
const respond = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const submit = async (form: HTMLFormElement, value: string) => {
  form.querySelector("input")!.value = value;
  form.dispatchEvent(new Event("submit", { cancelable: true }));
  await vi.waitFor(() => expect(form.querySelector("button[type=submit]")).not.toHaveProperty("disabled", true));
};

describe("pod name validation", () => {
  test("trims, counts characters, rejects control chars", () => {
    expect(checkPodName("  Jesus's racquet ")).toEqual({ name: "Jesus's racquet", error: null });
    expect(checkPodName("   ").error).toMatch(/Enter a name/);
    expect(checkPodName("a".repeat(40)).error).toBeNull();
    expect(checkPodName("a".repeat(41)).error).toMatch(/at most 40/);
    expect(checkPodName("😀".repeat(40)).error).toBeNull(); // code points, not UTF-16 units
    expect(checkPodName("bad\u0007name").error).toMatch(/printable/);
    expect(checkPodName("two\nlines").error).toMatch(/printable/);
    expect(checkPodName("a‮b").error).toMatch(/printable/);
  });
});

describe("rename form", () => {
  afterEach(() => vi.unstubAllGlobals());

  test("invalid input shows the error next to the field and sends nothing", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const form = renameForm(pod, () => {}, () => {});
    await submit(form, "   ");
    const input = form.querySelector("input")!;
    const err = form.querySelector(".field-error")!;
    expect(err.textContent).toMatch(/Enter a name/);
    expect(input.getAttribute("aria-invalid")).toBe("true");
    expect(input.getAttribute("aria-describedby")).toBe(err.id);
    expect(form.querySelector(`label[for="${input.id}"]`)).not.toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  test("PATCH carries the CSRF header and a JSON body; the saved pod is passed on", async () => {
    const calls: { url: string; init: RequestInit }[] = [];
    vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      if (url === "/api/me") return respond(200, { user: null, csrf: "tok-9" });
      return respond(200, { ...pod, name: "New name" });
    });
    await me(true);
    const saved: Pod[] = [];
    await submit(renameForm(pod, (p) => saved.push(p), () => {}), "  New name ");
    const patch = calls.find((c) => c.url === "/api/me/pods/pod_1")!;
    expect(patch.init.method).toBe("PATCH");
    expect((patch.init.headers as Record<string, string>)["x-csrf-token"]).toBe("tok-9");
    expect((patch.init.headers as Record<string, string>)["content-type"]).toBe("application/json");
    expect(JSON.parse(patch.init.body as string)).toEqual({ name: "New name" });
    expect(saved.map((p) => p.name)).toEqual(["New name"]);
  });

  test("server invalid_name is shown next to the field", async () => {
    vi.stubGlobal("fetch", async (url: string) => (url === "/api/me" ? respond(200, { user: null, csrf: "t" }) : respond(400, { error: "invalid_name" })));
    await me(true);
    const form = renameForm(pod, () => {}, () => {});
    await submit(form, "ok");
    expect(form.querySelector(".field-error")!.textContent).toMatch(/1-40 printable/);
  });

  test("a <script> name stays text", () => {
    const form = renameForm({ ...pod, name: "<script>alert(1)</script>" }, () => {}, () => {});
    expect(form.querySelector("input")!.value).toBe("<script>alert(1)</script>");
    expect(form.querySelector("script")).toBeNull();
  });
});

describe("mock pods API", () => {
  beforeAll(async () => {
    await installMock();
    await me(true);
  });

  test("lists pods, renames with the CSRF token, rejects bad names, 404s unknown pods", async () => {
    const before = await api.pods();
    expect(before.map((p) => p.hw_id)).toContain("OSO-4C99");
    const renamed = await api.renamePod("pod_2", "  <script>x</script> ");
    expect(renamed.name).toBe("<script>x</script>");
    expect((await api.pods()).find((p) => p.id === "pod_2")!.name).toBe("<script>x</script>");
    await expect(api.renamePod("pod_2", "")).rejects.toMatchObject({ status: 400, code: "invalid_name" });
    await expect(api.renamePod("pod_2", "x".repeat(41))).rejects.toMatchObject({ status: 400, code: "invalid_name" });
    await expect(api.renamePod("pod_404", "ok")).rejects.toMatchObject({ status: 404 });
    const r = await fetch("/api/me/pods/pod_1", { method: "PATCH", body: JSON.stringify({ name: "no csrf" }) });
    expect(r.status).toBe(403);
  });
});
