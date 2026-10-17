import { existsSync, readFileSync, statSync } from "node:fs";
import type { ServerResponse } from "node:http";
import { extname, join, resolve, sep } from "node:path";
import { defineConfig, type Plugin } from "vite";

const TYPES: Record<string, string> = {
  ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css", ".wasm": "application/wasm",
  ".json": "application/json", ".png": "image/png", ".obj": "text/plain", ".bin": "application/octet-stream",
};

/** Reads `rel` under `root`; null when it escapes `root`, is missing or is not a file (resolve + prefix check). */
function readUnder(root: string, rel: string): Buffer | null {
  const file = resolve(root, "." + sep + decodeURIComponent(rel));
  if (!file.startsWith(root + sep)) return null;
  try {
    return statSync(file).isFile() ? readFileSync(file) : null;
  } catch {
    return null;
  }
}

/** `npm run dev:mock` only. /viewer/ serves the real bundle (web/dist/viewer, from web/arena/build.sh) or falls back to
 *  mock-viewer/; /api/matches/<id>/replay.bin serves .mock-replays/<id>.bin (scripts/make-mock-replays.sh), else 404. */
const mockViewer = (): Plugin => ({
  name: "oso-mock-viewer",
  configureServer(server) {
    const dir = import.meta.dirname;
    const real = resolve(dir, "../web/dist/viewer");
    const useReal = existsSync(join(real, "index.html"));
    const root = useReal ? real : resolve(dir, "mock-viewer");
    if (!useReal) console.info("[oso] /viewer/ is the placeholder: run web/arena/build.sh for the real viewer");
    const send = (res: ServerResponse, status: number, type: string, body: Buffer | string) => {
      res.statusCode = status;
      res.setHeader("content-type", type);
      res.setHeader("content-length", Buffer.byteLength(body));
      res.end(body);
    };
    server.middlewares.use("/viewer/", (req, res) => {
      const rel = (req.url ?? "/").split("?")[0].replace(/^\//, "") || "index.html";
      try {
        const body = readUnder(root, rel);
        if (body) send(res, 200, TYPES[extname(rel)] ?? "text/plain", body);
        else send(res, 404, "text/plain", "not found");
      } catch {
        send(res, 400, "text/plain", "bad request");
      }
    });
    server.middlewares.use((req, res, next) => {
      const m = /^\/api\/matches\/(\d+)\/replay\.bin(\?|$)/.exec(req.url ?? "");
      if (!m) return next();
      const body = readUnder(resolve(dir, ".mock-replays"), `${m[1]}.bin`);
      if (body) send(res, 200, "application/octet-stream", body);
      else send(res, 404, "application/json", JSON.stringify({ error: "not_found", detail: "No mock replay for this match. Run site/scripts/make-mock-replays.sh." }));
    });
  },
});

export default defineConfig(({ mode }) => ({
  // Served at the domain root by the Pi agent's server (history routing: every page path falls back to index.html)
  base: "/",
  define: { __MOCK__: JSON.stringify(mode === "mock") },
  build: {
    target: "es2022",
    outDir: "dist",
    assetsInlineLimit: 0, // strict CSP: no data: URLs or inline anything
    modulePreload: { polyfill: false },
  },
  plugins: mode === "mock" ? [mockViewer()] : [],
  server: { host: true },
  test: { include: ["test/**/*.test.ts"], environment: "happy-dom" },
}));
