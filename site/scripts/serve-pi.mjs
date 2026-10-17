// Local stand-in for the Pi's static serving, for checking the site and the real viewer together under the
// production CSP. Not the Arena API: build the site with `--mode mock` so the API is faked in the page.
//
//   (cd .. && web/arena/build.sh)           # -> web/dist/viewer/
//   npx vite build --mode mock
//   OSO_REPLAY=../out/e2e/match.bin node scripts/serve-pi.mjs   # http://localhost:8090
//
// Every /api/matches/<id>/replay.bin answers with OSO_REPLAY (gzip-encoded, like the Pi may send it).
import { createReadStream, existsSync, statSync } from "node:fs";
import { createServer } from "node:http";
import { extname, join, normalize, resolve } from "node:path";
import { createGzip } from "node:zlib";

const here = import.meta.dirname;
const SITE = resolve(here, "../dist");
const VIEWER = resolve(here, "../../web/dist/viewer");
const REPLAY = process.env.OSO_REPLAY ? resolve(process.env.OSO_REPLAY) : null;
const PORT = Number(process.env.PORT ?? 8090);

// site/README.md, "For the Pi agent"; the viewer additionally needs 'wasm-unsafe-eval'
const CSP =
  "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' https://avatars.githubusercontent.com; " +
  "connect-src 'self'; frame-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'";
const VIEWER_CSP = CSP.replace("script-src 'self'", "script-src 'self' 'wasm-unsafe-eval'");

const TYPES = {
  ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml",
  ".wasm": "application/wasm", ".woff2": "font/woff2", ".json": "application/json", ".obj": "text/plain",
  ".mtl": "text/plain", ".gltf": "model/gltf+json", ".bin": "application/octet-stream", ".png": "image/png", ".jpg": "image/jpeg",
};

/** `root` + url path, or null when it escapes root or is not a file. */
function fileIn(root, path) {
  const full = normalize(join(root, decodeURIComponent(path)));
  if (!full.startsWith(root)) return null;
  return existsSync(full) && statSync(full).isFile() ? full : null;
}

function send(res, file, csp) {
  res.writeHead(200, { "content-type": TYPES[extname(file)] ?? "application/octet-stream", "content-security-policy": csp });
  createReadStream(file).pipe(res);
}

createServer((req, res) => {
  const path = new URL(req.url, "http://x").pathname;
  if (/^\/api\/matches\/\d+\/replay\.bin$/.test(path)) {
    if (!REPLAY) {
      res.writeHead(500, { "content-type": "application/json" });
      return res.end(JSON.stringify({ error: "no_replay", detail: "Set OSO_REPLAY to a replay.bin" }));
    }
    res.writeHead(200, { "content-type": "application/octet-stream", "content-encoding": "gzip" });
    return createReadStream(REPLAY).pipe(createGzip()).pipe(res);
  }
  if (path.startsWith("/viewer/")) {
    const file = fileIn(VIEWER, path.slice("/viewer".length));
    if (file) return send(res, file, VIEWER_CSP);
    res.writeHead(404);
    return res.end("not found");
  }
  // History routing: real files as is, every other path gets the app shell
  send(res, fileIn(SITE, path) ?? join(SITE, "index.html"), CSP);
}).listen(PORT, () => {
  console.log(`site ${SITE}\nviewer ${VIEWER}\nreplay ${REPLAY ?? "(none)"}\nhttp://localhost:${PORT}`);
});
