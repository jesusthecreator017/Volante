import { defineConfig } from "vite";

export default defineConfig({
  // Relative base: the same build works at a domain root, a sub-path on the Pi, and inside the Capacitor apps.
  base: "./",
  build: { target: "es2022", outDir: "dist", assetsInlineLimit: 0 },
  server: { host: true },
  test: { include: ["test/**/*.test.ts"] },
});
