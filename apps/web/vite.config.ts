/// <reference types="vitest/config" />
import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Resolve the shared Zod contracts to their SOURCE (not a build artifact) so the
// web app and `@edis/ts-contracts` are type-checked against the single source of
// truth. Mirrors the `@edis/contracts` path alias in tsconfig.json.
const contractsSrc = fileURLToPath(
  new URL("../../libs/edis-ts-contracts/src/index.ts", import.meta.url),
);
const srcDir = fileURLToPath(new URL("./src", import.meta.url));

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@edis/contracts": contractsSrc,
      "@": srcDir,
    },
  },
  server: {
    port: 5173,
    strictPort: false,
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./test/setup.ts"],
    css: false,
    restoreMocks: true,
    clearMocks: true,
  },
});
