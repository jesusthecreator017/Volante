#!/usr/bin/env node
// oso Pi server: static hosting of the built web app + a tiny session exchange with the video pipeline.
// Node >= 18, no dependencies. Usage:
//   node server/server.mjs [--port 8080] [--dist dist] [--data server/data] [--cert cert.pem --key key.pem]
//
// Why a backend at all (static files would serve the app): the video pipeline runs on the Pi/desktop and needs each
// session's imu.bin, and the replay.bin it produces has to get back to the phones. This server is just a folder per
// session that both sides read and write:
//   data/sessions/<id>/imu.bin     <- PUT by the app      -> read by oso-imu (Rust pipeline)
//   data/sessions/<id>/meta.json   <- PUT by the app (name, summary, sync markers)
//   data/sessions/<id>/replay.bin  <- written by the pipeline (or PUT) -> GET by the app's Replay page

import { createServer as createHttp } from "node:http";
import { createServer as createHttps } from "node:https";
import { createReadStream, existsSync, readFileSync } from "node:fs";
import { mkdir, readdir, readFile, rename, stat, writeFile } from "node:fs/promises";
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
  res.setHeader("access-control-allow-methods", "GET, PUT, OPTIONS");
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
    send(res, e.code === 413 ? 413 : 500, { error: String(e.message ?? e) });
  }
}

const server =
  args.cert && args.key
    ? createHttps({ cert: readFileSync(args.cert), key: readFileSync(args.key) }, handle)
    : createHttp(handle);
server.listen(PORT, () => {
  const proto = args.cert ? "https" : "http";
  console.log(`oso server on ${proto}://0.0.0.0:${PORT}  dist=${DIST}  data=${DATA}`);
  if (!existsSync(join(DIST, "index.html"))) console.warn("warning: dist/index.html missing — run `npm run build` first");
  if (!args.cert) console.warn("note: Web Bluetooth needs HTTPS on any host but localhost — see README (Pi hosting)");
});
