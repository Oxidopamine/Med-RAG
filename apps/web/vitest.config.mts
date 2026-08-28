import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";
import tsconfigPaths from "vite-tsconfig-paths";

export default defineConfig({
  plugins: [tsconfigPaths(), react()],
  test: {
    environment: "jsdom",
    exclude: ["e2e/**", "node_modules/**", ".next/**"],
    setupFiles: ["./vitest.setup.ts"],
    /*
     * Vitest's 5s default is a wall this suite hits for reasons that have nothing to do
     * with the code under test. A `user-event` interaction sequence in jsdom advances real
     * timers per keystroke, so the composer's longest test sits around four seconds on its
     * own - and goes over the moment the file count grows and the workers start competing
     * for the machine. The failures were parallelism; raising the ceiling is the honest fix
     * rather than making those tests type less and assert less.
     */
    testTimeout: 20_000,
  },
});
