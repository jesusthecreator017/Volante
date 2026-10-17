#!/usr/bin/env node
// oso Pi server: static hosting of the built web app + a tiny session exchange with the video pipeline.
// Node >= 18, no dependencies. Usage:
//   node server/server.mjs [--port 8080] [--dist dist] [--data server/data] [--cert cert.pem --key key.pem [--http-port 8080]]
//
// Why a backend at all (static files would serve the app): the video pipeline runs on the Pi/desktop and needs each
// session's imu.bin, and the replay.bin it produces has to get back to the phones. This server is just a folder per
// session that both sides read and write:
//   data/sessions/<id>/imu.bin     <- PUT by the app      -> read by oso-imu (Rust pipeline)
//   data/sessions/<id>/meta.json   <- PUT by the app (name, summary, sync markers)
//   data/sessions/<id>/replay.bin  <- written by the pipeline (or PUT) -> GET by the app's Replay page
//
// Fitbit (Google Health API) also goes through here, so every client (iPhone, web) gets heart rate from one place:
// the iPhone app signs in to Google (iOS OAuth client, PKCE) and hands the refresh token to the Pi once; the Pi
// keeps it in data/fitbit.json (mode 600, never served back) and answers /api/fitbit/heart-rate.
//   POST   /api/fitbit/token        {"clientId", "refreshToken"}
//   GET    /api/fitbit/status       {"connected": bool}
//   DELETE /api/fitbit/token
//   GET    /api/fitbit/heart-rate?from=<RFC 3339>&to=<RFC 3339>  -> [{"t": epoch s, "bpm": n}]

import { createServer as createHttp } from "node:http";
import { createServer as createHttps } from "node:https";
import { createReadStream, existsSync, readFileSync } from "node:fs";
import { mkdir, readdir, readFile, rename, rm, stat, writeFile } from "node:fs/promises";
import { extname, join, normalize, resolve, sep } from "node:path";
import { createGzip } from "node:zlib";
import { fileURLToPath } from "node:url";

const here = fileURLToPath(new URL(".", import.meta.url));
const args = Object.fromEntries(
  process.argv.slice(2).reduce((acc, a, i, all) => (a.startsWith("--") ? [...acc, [a.slice(2), all[i + 1]]] : acc), []),
);
const PORT = Number(args.port ?? process.env.PORT ?? 8080);
const DIST = resolve(args.dist ?? join(here, "..", "dist"));
const DATA = resolve(args.data ?? join(here, "data"));
const SESSIONS = join(DATA, "sessions");
const MAX_UPLOAD = 256 * 1024 * 1024; // ~4 h at 500 Hz is 130 MB
const FILES = new Set(["imu.bin", "meta.json", "replay.bin"]);
const ID_RE = /^[A-Za-z0-9_-]{1,64}$/;
const FITBIT = join(DATA, "fitbit.json");
// Overridable for tests (a fake Google on localhost)
const GOOGLE_HEALTH = process.env.OSO_GOOGLE_HEALTH ?? "https://health.googleapis.com/v4";
const GOOGLE_TOKEN = process.env.OSO_GOOGLE_TOKEN ?? "https://oauth2.googleapis.com/token";

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json",
  ".wasm": "application/wasm",
  ".bin": "application/octet-stream",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".svg": "image/svg+xml",
  ".obj": "text/plain",
  ".mtl": "text/plain",
  ".hdr": "application/octet-stream",
  ".ts": "text/plain",
};
const COMPRESSIBLE = new Set([".html", ".js", ".mjs", ".css", ".json", ".wasm", ".obj", ".mtl", ".hdr", ".ts"]);

function send(res, code, body, headers = {}) {
  res.writeHead(code, { "content-type": "application/json", ...headers });
  res.end(typeof body === "string" ? body : JSON.stringify(body));
}

function cors(req, res) {
  // The Capacitor apps (capacitor://localhost, https://localhost) and dev servers call the API cross-origin.
  res.setHeader("access-control-allow-origin", req.headers.origin ?? "*");
  res.setHeader("vary", "origin");
  res.setHeader("access-control-allow-methods", "GET, PUT, POST, DELETE, OPTIONS");
  res.setHeader("access-control-allow-headers", "content-type");
}

async function readBody(req) {
  const chunks = [];
  let n = 0;
  for await (const c of req) {
    n += c.length;
    if (n > MAX_UPLOAD) throw Object.assign(new Error("too large"), { code: 413 });
    chunks.push(c);
  }
  return Buffer.concat(chunks);
}

function validImu(buf) {
  return buf.length >= 16 && buf.subarray(0, 4).toString("latin1") === "OSOI" && buf.readUInt16LE(4) === 1 && (buf.length - 16) % 18 === 0;
}
function validReplay(buf) {
  return buf.length >= 8 && buf.subarray(0, 4).toString("latin1") === "OSOR";
}

async function api(req, res, parts) {
  cors(req, res);
  if (req.method === "OPTIONS") return send(res, 204, "");
  if (parts[0] === "health") return send(res, 200, { app: "oso", version: 1 });
  if (parts[0] === "fitbit") return fitbit(req, res, parts.slice(1));
  if (parts[0] !== "sessions") return send(res, 404, { error: "not found" });
  if (parts.length === 1 && req.method === "GET") {
    await mkdir(SESSIONS, { recursive: true });
    const out = [];
    for (const id of await readdir(SESSIONS)) {
      const dir = join(SESSIONS, id);
      const meta = await readFile(join(dir, "meta.json"), "utf8").then(JSON.parse).catch(() => ({ id }));
      out.push({ ...meta, id, hasImu: existsSync(join(dir, "imu.bin")), hasReplay: existsSync(join(dir, "replay.bin")) });
    }
    return send(res, 200, out);
  }
  const [, id, file] = parts;
  if (!ID_RE.test(id ?? "") || !FILES.has(file ?? "")) return send(res, 400, { error: "bad session id or file" });
  const path = join(SESSIONS, id, file);
  if (req.method === "GET") {
    if (!existsSync(path)) return send(res, 404, { error: "not found" });
    res.writeHead(200, { "content-type": MIME[extname(file)] ?? "application/octet-stream", "content-length": (await stat(path)).size });
    return createReadStream(path).pipe(res);
  }
  if (req.method === "PUT") {
    const body = await readBody(req);
    if (file === "imu.bin" && !validImu(body)) return send(res, 422, { error: "not a valid imu.bin (data-format.md)" });
    if (file === "replay.bin" && !validReplay(body)) return send(res, 422, { error: "not a replay.bin (magic OSOR)" });
    if (file === "meta.json") {
      try {
        JSON.parse(body.toString("utf8"));
      } catch {
        return send(res, 422, { error: "invalid JSON" });
      }
    }
    await mkdir(join(SESSIONS, id), { recursive: true });
    // Write atomically so the pipeline never reads a half-written file
    await writeFile(path + ".tmp", body);
    await rename(path + ".tmp", path);
    return send(res, 200, { ok: true, bytes: body.length });
  }
  return send(res, 405, { error: "method not allowed" });
}

// ---------------------------------------------------------------- Fitbit (Google Health API)

let access = null; // { token, expires } in memory only

async function fitbitCreds() {
  return JSON.parse(await readFile(FITBIT, "utf8").catch(() => "null"));
}

async function accessToken() {
  if (access && Date.now() < access.expires) return access.token;
  const c = await fitbitCreds();
  if (!c) throw Object.assign(new Error("Fitbit not connected: sign in from the iPhone app"), { code: 409 });
  // iOS OAuth clients have no secret: the refresh grant needs only the client id
  const r = await fetch(GOOGLE_TOKEN, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ client_id: c.clientId, refresh_token: c.refreshToken, grant_type: "refresh_token" }),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok || !j.access_token) throw Object.assign(new Error(`Google token refresh failed: ${r.status} ${j.error ?? ""}`), { code: 502 });
  access = { token: j.access_token, expires: Date.now() + ((j.expires_in ?? 3600) - 60) * 1000 };
  return access.token;
}

/** A DataPoint's heart rate, found by its fields: { heartRate: { sampleTime: { physicalTime }, beatsPerMinute } } */
function heartRatePoint(p) {
  for (const rec of Object.values(p ?? {})) {
    const time = rec?.sampleTime?.physicalTime;
    if (time && rec.beatsPerMinute != null) return { t: Date.parse(time) / 1000, bpm: Number(rec.beatsPerMinute) };
  }
  return null;
}

async function fitbit(req, res, parts) {
  const [what] = parts;
  if (what === "status" && req.method === "GET") return send(res, 200, { connected: !!(await fitbitCreds()) });
  if (what === "token" && req.method === "POST") {
    let b;
    try {
      b = JSON.parse((await readBody(req)).toString("utf8"));
    } catch {
      return send(res, 422, { error: "invalid JSON" });
    }
    if (typeof b.clientId !== "string" || typeof b.refreshToken !== "string") return send(res, 422, { error: "clientId and refreshToken required" });
    await mkdir(DATA, { recursive: true });
    await writeFile(FITBIT + ".tmp", JSON.stringify({ clientId: b.clientId, refreshToken: b.refreshToken }), { mode: 0o600 });
    await rename(FITBIT + ".tmp", FITBIT);
    access = null;
    return send(res, 200, { ok: true });
  }
  if (what === "token" && req.method === "DELETE") {
    await rm(FITBIT, { force: true });
    access = null;
    return send(res, 200, { ok: true });
  }
  if (what === "heart-rate" && req.method === "GET") {
    const q = new URL(req.url, "http://x").searchParams;
    const from = q.get("from");
    const to = q.get("to");
    if (!from || !to || isNaN(Date.parse(from)) || isNaN(Date.parse(to))) return send(res, 400, { error: "from and to (RFC 3339) required" });
    const f = "heart_rate.sample_time.physical_time";
    const filter = `${f} >= "${new Date(from).toISOString()}" AND ${f} < "${new Date(to).toISOString()}"`;
    const out = [];
    let page = "";
    do {
      const url = `${GOOGLE_HEALTH}/users/me/dataTypes/heart-rate/dataPoints?` +
        new URLSearchParams({ filter, pageSize: "10000", ...(page ? { pageToken: page } : {}) });
      const r = await fetch(url, { headers: { authorization: `Bearer ${await accessToken()}` } });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) return send(res, 502, { error: `Google Health API ${r.status}`, detail: j.error?.message ?? null });
      for (const p of j.dataPoints ?? []) {
        const s = heartRatePoint(p);
        if (s && Number.isFinite(s.t) && Number.isFinite(s.bpm)) out.push(s);
      }
      page = j.nextPageToken ?? "";
    } while (page);
    out.sort((a, b) => a.t - b.t);
    return send(res, 200, out);
  }
  return send(res, 404, { error: "not found" });
}

async function staticFile(req, res, urlPath) {
  let rel = decodeURIComponent(urlPath);
  if (rel.endsWith("/")) rel += "index.html";
  const path = normalize(join(DIST, rel));
  if (!path.startsWith(DIST + sep) && path !== DIST) return send(res, 403, { error: "forbidden" });
  let st = await stat(path).catch(() => null);
  if (!st || !st.isFile()) return send(res, 404, { error: "not found" }, { "content-type": "text/plain" });
  const ext = extname(path);
  const headers = {
    "content-type": MIME[ext] ?? "application/octet-stream",
    // hashed bundles are immutable; everything else revalidates
    "cache-control": rel.startsWith("/assets/") ? "public, max-age=31536000, immutable" : "no-cache",
  };
  if (COMPRESSIBLE.has(ext) && /\bgzip\b/.test(req.headers["accept-encoding"] ?? "")) {
    res.writeHead(200, { ...headers, "content-encoding": "gzip", vary: "accept-encoding" });
    return createReadStream(path).pipe(createGzip({ level: 6 })).pipe(res);
  }
  res.writeHead(200, { ...headers, "content-length": st.size });
  createReadStream(path).pipe(res);
}

async function handle(req, res) {
  try {
    const url = new URL(req.url ?? "/", "http://x");
    const parts = url.pathname.split("/").filter(Boolean);
    if (parts[0] === "api") return await api(req, res, parts.slice(1));
    if (req.method !== "GET" && req.method !== "HEAD") return send(res, 405, { error: "method not allowed" });
    return await staticFile(req, res, url.pathname);
  } catch (e) {
    send(res, [409, 413, 502].includes(e.code) ? e.code : 500, { error: String(e.message ?? e) });
  }
}

const tls = args.cert && args.key;
const server = tls ? createHttps({ cert: readFileSync(args.cert), key: readFileSync(args.key) }, handle) : createHttp(handle);
server.listen(PORT, () => {
  console.log(`oso server on ${tls ? "https" : "http"}://0.0.0.0:${PORT}  dist=${DIST}  data=${DATA}`);
  if (!existsSync(join(DIST, "index.html"))) console.warn("warning: dist/index.html missing — run `npm run build` first");
  if (!tls) console.warn("note: Web Bluetooth needs HTTPS on any host but localhost — see deploy/pi/README.md");
});
// With TLS, --http-port adds a plain-http listener on the same handler: the sideloaded iPhone/Watch apps talk http on
// the LAN (ATS allows local networking) without trusting the self-signed certificate; browsers use https.
if (tls && args["http-port"]) {
  createHttp(handle).listen(Number(args["http-port"]), () => console.log(`oso server on http://0.0.0.0:${args["http-port"]}`));
}
