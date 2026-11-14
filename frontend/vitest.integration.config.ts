import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Integration tests run against a *real* running backend. They are separated
// from the unit/component suite so the default `npm test` never touches the
// network. Run with:
//   BACKEND_BASE_URL=http://127.0.0.1:8000 npm run test:integration
export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: "node",
    include: ["src/test/integration/**/*.test.ts"],
    testTimeout: 20000,
    hookTimeout: 20000,
    // Run serially: the suite mutates shared backend state.
    fileParallelism: false,
    sequence: { concurrent: false },
  },
});
