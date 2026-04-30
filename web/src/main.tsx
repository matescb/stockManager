// Sentry init must run before any other module — keep this import first.
import "./instrument";

import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import * as Sentry from "@sentry/react";
import App from "./App";
import { ThemeProvider, bootTheme } from "./lib/theme";
import ThemedToaster from "./components/ThemedToaster";
import "./index.css";

// Apply the persisted/system theme class on <html> before mount to avoid a
// flash of the wrong theme.
bootTheme();

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
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
