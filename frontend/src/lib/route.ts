// URL-driven routing (no router dependency — matches the minimalist ?sheet= /
// SheetList pattern). Pure + side-effect-free so BOTH entrypoints (the public
// index.tsx and the SSO build's index.aftership.tsx) share ONE route resolver
// without importing a module that mounts React.
//   * ?inbox (or ?page=inbox)  → the per-user cross-sheet InboxPage
//   * ?sheet=X&dashboard=1      → that sheet's process ProcessDashboard
//   * ?sheet=X                  → the connected App (Proposed entry view)
//   * (none)                    → the SheetList home
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
