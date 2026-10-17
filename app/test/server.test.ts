// The Pi server (server/server.mjs) end to end: real process, temp data folder, a fake Google on localhost.
import { spawn, type ChildProcess } from "node:child_process";
import { mkdtempSync, readFileSync, statSync } from "node:fs";
import { createServer, type Server } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterAll, beforeAll, expect, test } from "vitest";
import { sampleBytes } from "./helpers";

let pi: ChildProcess;
let google: Server;
let base = "";
const data = mkdtempSync(join(tmpdir(), "oso-pi-"));
const seen: { refreshes: number; auth: string[]; filters: string[] } = { refreshes: 0, auth: [], filters: [] };

const listen = (s: Server) => new Promise<number>((r) => s.listen(0, "127.0.0.1", () => r((s.address() as { port: number }).port)));

beforeAll(async () => {
  google = createServer(async (req, res) => {
    const url = new URL(req.url!, "http://x");
    if (url.pathname === "/token") {
      let body = "";
      for await (const c of req) body += c;
      const p = new URLSearchParams(body);
      seen.refreshes++;
      const ok = p.get("refresh_token") === "r-1" && p.get("client_id") === "c.apps.googleusercontent.com";
      res.writeHead(ok ? 200 : 400, { "content-type": "application/json" });
      return res.end(JSON.stringify(ok ? { access_token: "a-1", expires_in: 3600 } : { error: "invalid_grant" }));
    }
    seen.auth.push(req.headers.authorization ?? "");
    seen.filters.push(url.searchParams.get("filter") ?? "");
    const page = url.searchParams.get("pageToken");
    const point = (t: string, bpm: string) => ({ heartRate: { sampleTime: { physicalTime: t, utcOffset: "0s" }, beatsPerMinute: bpm } });
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify(page
      ? { dataPoints: [point("2026-10-17T01:00:02Z", "120")] }
      : { dataPoints: [point("2026-10-17T01:00:01Z", "110"), point("2026-10-17T01:00:00Z", "100")], nextPageToken: "p2" }));
  });
  const gport = await listen(google);
  // a free port for the Pi server: take one, release it
  const probe = createServer();
  const port = await listen(probe);
  await new Promise((r) => probe.close(r));
  pi = spawn(process.execPath, ["server/server.mjs", "--port", String(port), "--data", data, "--dist", data], {
    env: { ...process.env, OSO_GOOGLE_HEALTH: `http://127.0.0.1:${gport}/v4`, OSO_GOOGLE_TOKEN: `http://127.0.0.1:${gport}/token` },
    stdio: "pipe",
  });
  await new Promise<void>((r) => pi.stdout!.on("data", (d) => String(d).includes("oso server on") && r()));
  base = `http://127.0.0.1:${port}/api`;
});

afterAll(() => {
  pi?.kill();
  google?.close();
});

test("sessions: upload imu.bin + meta, list, and refuse a broken file", async () => {
  let r = await fetch(`${base}/sessions/s1/imu.bin`, { method: "PUT", body: sampleBytes() });
  expect(r.status).toBe(200);
  r = await fetch(`${base}/sessions/s1/meta.json`, { method: "PUT", body: JSON.stringify({ name: "watch 1", source: "watch" }) });
  expect(r.status).toBe(200);
  expect((await fetch(`${base}/sessions/s1/imu.bin`, { method: "PUT", body: new Uint8Array(20) })).status).toBe(422);
  const list = await (await fetch(`${base}/sessions`)).json();
  expect(list).toMatchObject([{ id: "s1", name: "watch 1", hasImu: true, hasReplay: false }]);
  expect(new Uint8Array(await (await fetch(`${base}/sessions/s1/imu.bin`)).arrayBuffer())).toEqual(sampleBytes());
});

test("fitbit: not connected until the phone hands over a token", async () => {
  expect(await (await fetch(`${base}/fitbit/status`)).json()).toEqual({ connected: false });
  const r = await fetch(`${base}/fitbit/heart-rate?from=2026-10-17T01:00:00Z&to=2026-10-17T01:10:00Z`);
  expect(r.status).toBe(409);
});

test("fitbit: token stored privately, heart rate fetched across pages and sorted", async () => {
  let r = await fetch(`${base}/fitbit/token`, {
    method: "POST",
    body: JSON.stringify({ clientId: "c.apps.googleusercontent.com", refreshToken: "r-1" }),
  });
  expect(r.status).toBe(200);
  expect(statSync(join(data, "fitbit.json")).mode & 0o777).toBe(0o600);
  expect(JSON.parse(readFileSync(join(data, "fitbit.json"), "utf8")).refreshToken).toBe("r-1");
  expect(await (await fetch(`${base}/fitbit/status`)).json()).toEqual({ connected: true });

  r = await fetch(`${base}/fitbit/heart-rate?from=2026-10-17T01:00:00Z&to=2026-10-17T01:10:00Z`);
  expect(r.status).toBe(200);
  const t0 = Date.parse("2026-10-17T01:00:00Z") / 1000;
  expect(await r.json()).toEqual([{ t: t0, bpm: 100 }, { t: t0 + 1, bpm: 110 }, { t: t0 + 2, bpm: 120 }]);
  expect(seen.auth).toEqual(["Bearer a-1", "Bearer a-1"]);
  expect(seen.filters[0]).toBe(
    'heart_rate.sample_time.physical_time >= "2026-10-17T01:00:00.000Z" AND heart_rate.sample_time.physical_time < "2026-10-17T01:10:00.000Z"',
  );
  // access token cached
  await fetch(`${base}/fitbit/heart-rate?from=2026-10-17T01:00:00Z&to=2026-10-17T01:10:00Z`);
  expect(seen.refreshes).toBe(1);

  expect((await fetch(`${base}/fitbit/token`, { method: "DELETE" })).status).toBe(200);
  expect(await (await fetch(`${base}/fitbit/status`)).json()).toEqual({ connected: false });
});
