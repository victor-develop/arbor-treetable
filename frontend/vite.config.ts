/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Standalone Vite + React shell. In dev it proxies /api to the Frappe backend
// so executeAction / getSheetSnapshot / agent.chat hit the real capability API.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: process.env.ARBOR_BACKEND_URL ?? "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    // Pin the jsdom document origin so history.replaceState with an absolute
    // http://localhost/ URL (used by the view-link tests) is same-origin and
    // not rejected as a cross-origin navigation.
    environmentOptions: { jsdom: { url: "http://localhost/" } },
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
