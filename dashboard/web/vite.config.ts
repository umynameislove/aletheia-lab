import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

/* `npm run dev` is the offline demo. `npm run dev:live` builds the LiveProductPort and forwards
   /api to the local bridge (python -m aletheia_lab.dashboard --store-root STORE). The bridge only
   accepts its own origin, so the dev proxy drops the browser's Origin header; both ends stay on
   127.0.0.1. */
const BRIDGE = process.env.P6_BRIDGE ?? "http://127.0.0.1:8765";

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  // `--mode bench` builds only the lineage benchmark page, into its own folder
  build: mode === "bench" ? { outDir: "dist-bench", rollupOptions: { input: "bench.html" } } : undefined,
  server: {
    host: "127.0.0.1",
    port: 5274,
    strictPort: true,
    proxy: mode === "live"
      ? {
          "/api": {
            target: BRIDGE,
            changeOrigin: true,
            configure: proxy => proxy.on("proxyReq", req => req.removeHeader("origin")),
          },
        }
      : undefined,
  },
}));
