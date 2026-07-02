// Runnable: bench-free (vitest + jsdom; no Frappe, no running app).
//
// ImpersonationBar (Feature: act-as). Drives ENTIRELY off whoami/snapshot viewer
// hints (never re-derives auth):
//   * non-admin, not impersonating  → renders nothing
//   * admin, not impersonating      → an "Act as…" picker (→ beginImpersonation)
//   * impersonating (any viewer)    → a prominent banner "Acting as <effective>
//                                     (as <real_user>) · Stop" (→ endImpersonation)
// It calls the injected onBegin/onStop callbacks; the shell owns the client call
// + the whoami/snapshot refetch that follows.

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ImpersonationBar } from "./ImpersonationBar";

describe("ImpersonationBar — visibility", () => {
  it("renders nothing for a non-admin who is not impersonating", () => {
    const { container } = render(
      <ImpersonationBar
        isAdmin={false}
        impersonating={false}
        effectiveUser="alice"
        realUser={null}
        onBegin={vi.fn()}
        onStop={vi.fn()}
      />,
    );
    expect(container.firstChild).toBeNull();
    expect(screen.queryByTestId("impersonation-bar")).toBeNull();
  });
});

describe("ImpersonationBar — admin picker (not impersonating)", () => {
  it("shows the Act as… picker and calls onBegin with the entered user", () => {
    const onBegin = vi.fn();
    render(
      <ImpersonationBar
        isAdmin
        impersonating={false}
        effectiveUser="admin@example.com"
        realUser={null}
        onBegin={onBegin}
        onStop={vi.fn()}
      />,
    );
    expect(screen.getByTestId("impersonation-picker")).toBeInTheDocument();
    expect(screen.queryByTestId("impersonation-banner")).toBeNull();

    fireEvent.change(screen.getByTestId("impersonation-user"), {
      target: { value: "owner@example.com" },
    });
    fireEvent.click(screen.getByTestId("impersonation-begin"));
    expect(onBegin).toHaveBeenCalledWith("owner@example.com", undefined);
  });

  it("passes an optional reason through to onBegin", () => {
    const onBegin = vi.fn();
    render(
      <ImpersonationBar
        isAdmin
        impersonating={false}
        effectiveUser="admin@example.com"
        realUser={null}
        onBegin={onBegin}
        onStop={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByTestId("impersonation-user"), {
      target: { value: "owner@example.com" },
    });
    fireEvent.change(screen.getByTestId("impersonation-reason"), {
      target: { value: "cover shift" },
    });
    fireEvent.click(screen.getByTestId("impersonation-begin"));
    expect(onBegin).toHaveBeenCalledWith("owner@example.com", "cover shift");
  });

  it("does not call onBegin when no user is entered", () => {
    const onBegin = vi.fn();
    render(
      <ImpersonationBar
        isAdmin
        impersonating={false}
        effectiveUser="admin@example.com"
        realUser={null}
        onBegin={onBegin}
        onStop={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("impersonation-begin"));
    expect(onBegin).not.toHaveBeenCalled();
  });
});

describe("ImpersonationBar — impersonation banner", () => {
  it("shows a banner naming the effective + real user and wires Stop to onStop", () => {
    const onStop = vi.fn();
    render(
      <ImpersonationBar
        isAdmin
        impersonating
        effectiveUser="owner@example.com"
        realUser="admin@example.com"
        onBegin={vi.fn()}
        onStop={onStop}
      />,
    );
    const banner = screen.getByTestId("impersonation-banner");
    expect(banner).toHaveTextContent("owner@example.com");
    expect(banner).toHaveTextContent("admin@example.com");
    // The picker is hidden while impersonating.
    expect(screen.queryByTestId("impersonation-picker")).toBeNull();

    fireEvent.click(screen.getByTestId("impersonation-stop"));
    expect(onStop).toHaveBeenCalledTimes(1);
  });

  it("shows the banner even for a viewer whose admin flag is false (impersonated identity)", () => {
    // While acting-as, is_admin reflects the IMPERSONATED user, which may be a
    // non-admin — the banner must still show so Stop is always reachable.
    render(
      <ImpersonationBar
        isAdmin={false}
        impersonating
        effectiveUser="owner@example.com"
        realUser="admin@example.com"
        onBegin={vi.fn()}
        onStop={vi.fn()}
      />,
    );
    expect(screen.getByTestId("impersonation-banner")).toBeInTheDocument();
    expect(screen.getByTestId("impersonation-stop")).toBeInTheDocument();
  });
});
