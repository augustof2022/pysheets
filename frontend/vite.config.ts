import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run dev` proxies the API to `pysheets --dev` (port 8765).
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: {
    outDir: "../src/pysheets/web",
    emptyOutDir: true,
    target: "es2020",
    chunkSizeWarningLimit: 900,
  },
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8765" },
  },
});
