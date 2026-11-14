import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// The backend base URL is injected at build/dev time via VITE_API_BASE_URL.
// It is read in exactly one place (src/api/client.ts) so no component ever
// needs to know a URL.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    // Integration tests need a live backend and are run via the separate
    // vitest.integration.config.ts, not the default unit/component run.
    exclude: ["node_modules", "dist", "src/test/integration/**"],
    css: false,
  },
});
