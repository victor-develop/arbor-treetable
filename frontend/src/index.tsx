import { createRoot } from "react-dom/client";
import App from "./App";
import { SheetList } from "./components/SheetList";
import { InboxPage } from "./components/InboxPage";
import { ProcessDashboard } from "./components/ProcessDashboard";
import { api as defaultClient, setAuthHeaderProvider } from "./api";
import { pickRoute, type Route } from "./lib/route";
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

// Route resolution (pickRoute) lives in ./lib/route (side-effect-free) so both
// this entry and the SSO build variant share it without importing a module that
// mounts React. Re-export the type here for existing importers.
export type { Route } from "./lib/route";

// Navigate by setting the query (thin shell; no history router). Kept here so the
// inbox/dashboard back + deep-link affordances share ONE navigation primitive.
function navigate(search: string): void {
  window.location.search = search;
}

function rootForRoute(route: Route): JSX.Element {
  switch (route.kind) {
    case "inbox":
      // Cross-sheet inbox. Ack reuses the acknowledge capability via executeAction;
      // opening a row deep-links to its sheet (+ node highlight is a future hook).
      return (
        <InboxPage
          client={defaultClient}
          onAck={(notification) =>
            void defaultClient.executeAction("acknowledge", { notification })
          }
          onOpen={({ sheet: s }) => navigate(`?sheet=${encodeURIComponent(s)}`)}
        />
      );
    case "dashboard":
      return (
        <main className="arbor-app arbor-dashboard-page">
          <header className="arbor-header">
            <div className="arbor-header-titles">
              <a
                className="arbor-back-link"
                data-testid="dashboard-back"
                href={`?sheet=${encodeURIComponent(route.sheet)}`}
              >
                ‹ Back to sheet
              </a>
              <h1>Process · {route.sheet}</h1>
            </div>
          </header>
          <ProcessDashboard client={defaultClient} sheet={route.sheet} />
        </main>
      );
    case "sheet":
      // Land in the Proposed (pending-overlaid) view on entry; toggle to Live to edit.
      return <App sheetName={route.sheet} initialViewMode="proposed" />;
    case "home":
      return <SheetList />;
  }
}

const root = document.getElementById("root");
if (root) {
  // NB: no <StrictMode> wrapper. Its dev-only double-invocation of effects fires
  // the mount refetch twice; under fast interaction the second refetch can land
  // mid-edit and clobber an optimistic commit. Production createRoot never
  // double-mounts, so this only ever bit the dev server + e2e timing.
  const route = pickRoute(new URLSearchParams(window.location.search));
  createRoot(root).render(rootForRoute(route));
}
