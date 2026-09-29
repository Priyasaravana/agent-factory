import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

// In dev, proxy /api to the engine; in compose, nginx does the same.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(__dirname, "src") } },
  build: {
    rollupOptions: {
      output: {
        // Long-lived vendor chunks: app changes don't bust the framework cache.
        manualChunks: {
          react: ["react", "react-dom", "react-router-dom", "@tanstack/react-query"],
          ui: ["@radix-ui/react-dialog", "@radix-ui/react-dropdown-menu", "@radix-ui/react-tooltip", "cmdk", "sonner", "lucide-react"],
          dnd: ["@dnd-kit/core", "@dnd-kit/sortable", "@dnd-kit/utilities"],
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
