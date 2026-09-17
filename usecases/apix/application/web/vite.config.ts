import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dashboard SPA. VITE_DASHBOARD_API_URL / VITE_CHAT_API_URL are read at
// build time (see src/api.ts). Dev server proxies /api to the FastAPI backend
// so cookies work same-origin in local development.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_DASHBOARD_API_ORIGIN || "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
