import { createRoot } from "react-dom/client";
import App from "./App";
import { SheetList } from "./components/SheetList";
import { InboxPage } from "./components/InboxPage";
import { ProcessDashboard } from "./components/ProcessDashboard";
import { api as defaultClient, setAuthHeaderProvider } from "./api";
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

// URL-driven routing (no router dependency — matches the minimalist ?sheet= /
// SheetList pattern). The entry switch resolves the current query into one of:
//   * ?inbox (or ?page=inbox)      → the per-user cross-sheet <InboxPage>
//   * ?sheet=X&dashboard=1         → that sheet's process <ProcessDashboard>
//   * ?sheet=X                     → the connected <App> (Proposed entry view)
//   * (none)                       → the <SheetList> home
// `pickRoot` is exported-in-spirit (pure over a URLSearchParams) so the routing
// is unit-testable without touching the DOM.
export type Route =
  | { kind: "inbox" }
  | { kind: "dashboard"; sheet: string }
  | { kind: "sheet"; sheet: string }
  | { kind: "home" };

export function pickRoute(params: URLSearchParams): Route {
  if (params.get("inbox") !== null || params.get("page") === "inbox") {
    return { kind: "inbox" };
  }
  const sheet = params.get("sheet") ?? undefined;
  if (sheet) {
    // ?dashboard (any value, incl. bare flag) opens that sheet's dashboard.
    if (params.get("dashboard") !== null) return { kind: "dashboard", sheet };
    return { kind: "sheet", sheet };
  }
  return { kind: "home" };
}

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
