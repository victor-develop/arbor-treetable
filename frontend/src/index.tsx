import { createRoot } from "react-dom/client";
import App from "./App";
import { setAuthHeaderProvider } from "./api";
import "./styles.css";

// Open-source entrypoint. The employee SSO build replaces this with a wrapper
// that mounts <AuthProviderEmployee> and registers setAuthHeaderProvider
// (ARCHITECTURE §10). Core never imports the SSO SDK.

// Standalone auth: send an injected token if present (a Frappe API key for
// headless/e2e via window.__ARBOR_AUTH__), else rely on the Frappe session
// cookie. The SSO build overrides this provider.
setAuthHeaderProvider(async () => {
  const token = (window as unknown as { __ARBOR_AUTH__?: string }).__ARBOR_AUTH__;
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = token;
  return headers;
});

// The sheet to load comes from ?sheet=… ; absent it renders the idle shell.
const sheet = new URLSearchParams(window.location.search).get("sheet") ?? undefined;

const root = document.getElementById("root");
if (root) {
  // NB: no <StrictMode> wrapper. Its dev-only double-invocation of effects fires
  // the mount refetch twice; under fast interaction the second refetch can land
  // mid-edit and clobber an optimistic commit. Production createRoot never
  // double-mounts, so this only ever bit the dev server + e2e timing.
  createRoot(root).render(<App sheetName={sheet} />);
}
