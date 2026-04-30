import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { ThemeProvider, bootTheme } from "./lib/theme";
import { initSentry } from "./lib/sentry";
import ThemedToaster from "./components/ThemedToaster";
import "./index.css";

// Apply the persisted/system theme class on <html> before mount to avoid a
// flash of the wrong theme.
bootTheme();

// Best-effort error reporting. No-ops when VITE_SENTRY_DSN is empty.
initSentry().catch(() => {/* SDK load failed — skip silently */});

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </QueryClientProvider>
      <ThemedToaster />
    </ThemeProvider>
  </React.StrictMode>
);
