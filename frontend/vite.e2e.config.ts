import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Throwaway e2e Vite config: same shell as vite.config.ts but proxies /api to the
// on-disk arbor bench on :8080 and serves on :5174, so e2e can run without
// touching the committed vite.config.ts (which targets :8000).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    strictPort: true,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8080",
        changeOrigin: true,
      },
    },
  },
});
