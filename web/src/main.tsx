// Sentry init must run before any other module — keep this import first.
import "./instrument";

import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import {
  MutationCache,
  QueryCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import * as Sentry from "@sentry/react";
import App from "./App";
import { ThemeProvider, bootTheme } from "./lib/theme";
import ThemedToaster from "./components/ThemedToaster";
import { ApiError } from "./lib/api";
import { authBus } from "./lib/queryKeys";
import "./index.css";

// Apply the persisted/system theme class on <html> before mount to avoid a
// flash of the wrong theme.
bootTheme();

/**
 * Centralised 401 handler (FE2-001).
 *
 * Pre-fix, every list page caught an `ApiError(401, …)` locally and
 * rendered an empty list — the user couldn't tell whether their
 * session had expired or the workspace really was empty. Now any 401
 * thrown by a query or mutation fires a single `authBus.emit("unauthorized")`
 * which `<AuthProvider>` listens for to drop session state and `<App>`
 * uses to redirect to /login while preserving the original location.
 *
 * The handler runs outside React, so we can't use `useNavigate()` here.
 * The auth bus pattern keeps this dependency-free and avoids tight
 * coupling to the router layer.
 */
function on401(err: unknown) {
  if (err instanceof ApiError && err.status === 401) {
    authBus.emit("unauthorized");
  }
}

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  queryCache: new QueryCache({ onError: on401 }),
  mutationCache: new MutationCache({ onError: on401 }),
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    {/* React 18 — wrap the tree in Sentry.ErrorBoundary so unhandled
        component errors are captured (React 19+ would use reactErrorHandler
        on createRoot instead). The fallback is intentionally minimal so a
        crash anywhere in the app still shows the user something. */}
    <Sentry.ErrorBoundary
      fallback={
        <div className="p-6 text-danger">
          Something went wrong — the error has been reported. Try reloading.
        </div>
      }
    >
      <ThemeProvider>
        <QueryClientProvider client={queryClient}>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </QueryClientProvider>
        <ThemedToaster />
      </ThemeProvider>
    </Sentry.ErrorBoundary>
  </React.StrictMode>
);
