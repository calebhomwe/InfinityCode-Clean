import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// TAURI_DEV_HOST is set by `tauri dev`; honoring it lets `vite --host` expose
// the UI on the LAN for phone testing. When unset, host stays false so the dev
// server binds localhost only (safe default).
const host = process.env.TAURI_DEV_HOST;

export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: [],
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    css: false,
  },
  server: {
    port: 1420,
    strictPort: true,
    host: host || false,
    hmr: host ? { protocol: "ws", host, port: 1421 } : undefined,
    watch: {
      ignored: ["**/src-tauri/**"],
    },
  },
  build: {
    // Split the heavy, Chat-only markdown/syntax libs out of the main chunk so
    // Build-mode users don't pay for them on first paint, and vendor code caches
    // independently of app code across releases.
    rollupOptions: {
      output: {
        manualChunks: {
          "vendor-react": ["react", "react-dom"],
          "vendor-markdown": ["react-markdown", "react-syntax-highlighter"],
        },
      },
    },
    chunkSizeWarningLimit: 900,
  },
});
