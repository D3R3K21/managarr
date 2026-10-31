import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The backend serves the built dist/ as static files; all API + MCP share the
// same origin in production, so we use relative URLs. In dev we proxy /api and
// /mcp to the local FastAPI backend.
export default defineConfig({
  plugins: [react()],
  base: "/",
  build: {
    outDir: "dist",
  },
  server: {
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
      "/mcp": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
});
