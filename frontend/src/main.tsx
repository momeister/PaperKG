import React from "react";
import ReactDOM from "react-dom/client";
import { HashRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import App from "./App";
import { installNativeExternalLinks } from "./native";
// Self-hosted Schriften (kein CDN — die App bleibt vollständig lokal).
import "@fontsource-variable/inter";
import "@fontsource-variable/bricolage-grotesque";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/500.css";
// Token- und Theme-Schicht vor dem Komponenten-Stylesheet laden.
import "./styles/tokens.css";
import "./styles/themes.css";
import "./styles/motion.css";
import "./styles.css";
import "./styles/hub.css";

// In the native shell, route target=_blank / window.open to the OS browser.
installNativeExternalLinks();

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1
    }
  }
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <HashRouter>
        <App />
      </HashRouter>
    </QueryClientProvider>
  </React.StrictMode>
);
