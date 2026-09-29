import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, proxy /api to the engine; in compose, nginx does the same.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": process.env.API_URL ?? "http://localhost:8000",
      "/auth": process.env.AUTH_URL ?? "http://localhost:8001",
    },
  },
  preview: {
    proxy: {
      "/api": process.env.API_URL ?? "http://localhost:8000",
      "/auth": process.env.AUTH_URL ?? "http://localhost:8001",
    },
  },
});
