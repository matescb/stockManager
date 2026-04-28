import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import basicSsl from "@vitejs/plugin-basic-ssl";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

// Serve the dev server over HTTPS when VITE_HTTPS=1. Required for the
// barcode scanner (getUserMedia) to work over a LAN address — browsers
// only allow camera access in a secure context, and "localhost" is the
// only HTTP exception.
const useHttps = process.env.VITE_HTTPS === "1";

export default defineConfig({
  plugins: [react(), ...(useHttps ? [basicSsl()] : [])],
  resolve: {
    alias: {
      "@": path.resolve(here, "src"),
    },
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api": {
        // Default targets the docker-compose 'backend' service. Set
        // VITE_API_PROXY to override when running outside Docker, e.g.
        //   VITE_API_PROXY=http://127.0.0.1:8765 npm run dev
        target: process.env.VITE_API_PROXY ?? "http://backend:8000",
        changeOrigin: true,
      },
    },
  },
});
