import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The dev proxy points /api at a local backend so the dashboard can be
// developed against `USE_SQLITE=1 manage.py runserver` with no CORS setup.
// In the pilot the dashboard is served from the same origin as the API,
// behind the ALB, so no CORS configuration exists in production either.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: process.env.VITE_PROXY_TARGET ?? "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["src/test/setup.ts"],
    globals: true,
  },
} as never);
