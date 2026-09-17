import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    proxy: {
      "/ops": { target: process.env.VITE_OPS_API_ORIGIN || "http://localhost:8100", changeOrigin: true, rewrite: (p) => p.replace(/^\/ops/, "") },
    },
  },
});
