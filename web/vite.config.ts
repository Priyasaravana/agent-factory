import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

const VENDOR_CHUNKS: Record<string, string[]> = {
  react: ["react", "react-dom", "react-router", "react-router-dom", "scheduler", "@tanstack/react-query", "@tanstack/query-core"],
  ui: ["@radix-ui", "cmdk", "sonner", "lucide-react"],
  dnd: ["@dnd-kit"],
};

// In dev, proxy /api to the engine; in compose, nginx does the same.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(import.meta.dirname, "src") } },
  build: {
    rollupOptions: {
      output: {
        // Long-lived vendor chunks: app changes don't bust the framework cache.
        // Vite 8 (Rolldown) takes manualChunks only as a function.
        manualChunks(id: string) {
          if (!id.includes("/node_modules/")) return undefined;
          for (const [chunk, packages] of Object.entries(VENDOR_CHUNKS)) {
            if (packages.some((name) => id.includes(`/node_modules/${name}/`))) return chunk;
          }
          return undefined;
        },
      },
    },
  },
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
