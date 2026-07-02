import { describe, expect, it } from "vitest";
import { pickRoute } from "./index";

// The entry switch is a pure function over a URLSearchParams (no DOM), so the
// four routes can be asserted directly. Guards the routing contract WS-PROC-FE
// depends on: ?inbox / ?page=inbox → inbox; ?sheet&dashboard → dashboard;
// ?sheet → the connected app; nothing → the SheetList home.
describe("index.tsx pickRoute", () => {
  const route = (qs: string) => pickRoute(new URLSearchParams(qs));

  it("no query → home (SheetList)", () => {
    expect(route("")).toEqual({ kind: "home" });
  });

  it("?sheet=X → the connected sheet app", () => {
    expect(route("?sheet=Alpha")).toEqual({ kind: "sheet", sheet: "Alpha" });
  });

  it("?inbox (bare flag) → the cross-sheet inbox", () => {
    expect(route("?inbox=1")).toEqual({ kind: "inbox" });
    expect(route("?inbox")).toEqual({ kind: "inbox" });
  });

  it("?page=inbox → the cross-sheet inbox (alias)", () => {
    expect(route("?page=inbox")).toEqual({ kind: "inbox" });
  });

  it("?sheet=X&dashboard=1 → that sheet's process dashboard", () => {
    expect(route("?sheet=Alpha&dashboard=1")).toEqual({ kind: "dashboard", sheet: "Alpha" });
  });

  it("?dashboard without a sheet falls back to home", () => {
    expect(route("?dashboard=1")).toEqual({ kind: "home" });
  });

  it("inbox takes precedence over a sheet param", () => {
    expect(route("?inbox=1&sheet=Alpha")).toEqual({ kind: "inbox" });
  });
});
