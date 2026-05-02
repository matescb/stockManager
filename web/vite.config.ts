import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import basicSsl from "@vitejs/plugin-basic-ssl";
import { sentryVitePlugin } from "@sentry/vite-plugin";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

// Serve the dev server over HTTPS when VITE_HTTPS=1. Required for the
// barcode scanner (getUserMedia) to work over a LAN address — browsers
// only allow camera access in a secure context, and "localhost" is the
// only HTTP exception.
const useHttps = process.env.VITE_HTTPS === "1";

// Source-map upload is handled by the CI web-build job (INFRA2-010) via
// `npx @sentry/cli sourcemaps upload` after `npm run build`. The
// sentryVitePlugin is retained as a no-op path for any developer who
// still has SENTRY_AUTH_TOKEN set locally; the build always produces
// hidden sourcemaps so CI can upload them regardless.
const sentryToken = process.env.SENTRY_AUTH_TOKEN;
const sentryOrg = process.env.SENTRY_ORG;
const sentryProject = process.env.SENTRY_PROJECT;
const enableSentryUpload = Boolean(sentryToken && sentryOrg && sentryProject);

export default defineConfig({
  // @ts-expect-error vitest config lives next to vite's; types come from vitest/config
  test: {
    setupFiles: ["./vitest.setup.ts"],
    // Most tests are pure helpers and run on the default node env. The
    // jsdom env (heavier startup) is opt-in: any file under a `__dom__`
    // directory or matching `*.dom.test.*` runs against jsdom so RTL
    // can render. See web/src/components/__dom__/ for the convention.
    environmentMatchGlobs: [
      ["**/__dom__/**", "jsdom"],
      ["**/*.dom.test.{ts,tsx}", "jsdom"],
    ],
    // Playwright lives under web/e2e/ and runs via `npm run test:e2e`,
    // not vitest — exclude it explicitly so vitest doesn't try to load
    // the spec files.
    exclude: ["**/node_modules/**", "**/dist/**", "**/e2e/**"],
    // TEST-013 / issue #115. CI runs `vitest run --coverage`; output
    // lives in web/coverage/ and is uploaded as an artifact. No
    // fail-under yet (issue says ratchet later).
    coverage: {
      provider: "v8",
      reporter: ["text", "html", "lcov"],
      reportsDirectory: "./coverage",
      exclude: [
        "**/*.d.ts",
        "**/*.config.*",
        "**/scripts/**",
        "**/__tests__/**",
        "**/*.test.{ts,tsx}",
        "node_modules/**",
        "dist/**",
      ],
    },
  },
  build: {
    // Always produce hidden sourcemaps so the CI job can upload them to
    // Sentry. "hidden" omits the //# sourceMappingURL= footer so browsers
    // don't fetch them; Dockerfile.prod strips the .map files before the
    // nginx image is assembled (Infra CRIT-5).
    sourcemap: "hidden",
    rollupOptions: {
      output: {
        // Carve heavy third-party deps into their own cached chunks so
        // the main `index-*.js` stays close to its lazy-route-only weight.
        // Sentry's SDK is the biggest single contributor (~280 KB);
        // splitting it puts that bytes-on-disk in its own file that
        // returning visitors hit out of cache.
        manualChunks: {
          sentry: [
            "@sentry/react",
          ],
        },
      },
    },
  },
  plugins: [
    react(),
    ...(useHttps ? [basicSsl()] : []),
    ...(enableSentryUpload
      ? [
          sentryVitePlugin({
            authToken: sentryToken,
            org: sentryOrg,
            project: sentryProject,
            // The release name is provided by VITE_APP_VERSION at build
            // time (compose passes the deploy's git SHA); fall back to
            // the plugin's git-based detection if absent.
            release: process.env.VITE_APP_VERSION
              ? { name: process.env.VITE_APP_VERSION }
              : undefined,
            telemetry: false,
          }),
        ]
      : []),
  ],
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
