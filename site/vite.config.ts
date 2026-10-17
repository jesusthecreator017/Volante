import { readFileSync } from "node:fs";
import { extname, join } from "node:path";
import { defineConfig, type Plugin } from "vite";

/** `npm run dev:mock` only: serves mock-viewer/ at /viewer/ (the real viewer bundle comes from the oso repo team). */
const mockViewer = (): Plugin => ({
  name: "oso-mock-viewer",
  configureServer(server) {
    server.middlewares.use("/viewer/", (req, res) => {
      const file = (req.url ?? "/").split("?")[0].replace(/^\//, "") || "index.html";
      const types: Record<string, string> = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css" };
      try {
        res.setHeader("content-type", types[extname(file)] ?? "text/plain");
        res.end(readFileSync(join(import.meta.dirname, "mock-viewer", file.replace(/\.\./g, ""))));
      } catch {
        res.statusCode = 404;
        res.end("not found");
      }
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
