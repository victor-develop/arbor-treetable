// Unit spec for AdminUsersPanel (Admin modal — Users tab). The table lists
// every account with admin/enabled toggles that dispatch onSetUser; the
// viewer's OWN row is inert (mirrors the backend self-guard on set_user).

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AdminUsersPanel } from "./AdminUsersPanel";
import type { UserRow } from "../api";

const USERS: UserRow[] = [
  { email: "admin@x.com", full_name: "Admin", is_admin: true, enabled: true, creation: "2026-01-01" },
  { email: "bob@x.com", full_name: "Bob", is_admin: false, enabled: true, creation: "2026-02-01" },
  { email: "carol@x.com", full_name: "Carol", is_admin: false, enabled: false, creation: "2026-03-01" },
];

describe("AdminUsersPanel", () => {
  it("renders a row per user with the current admin/enabled flags", () => {
    render(<AdminUsersPanel users={USERS} selfEmail="admin@x.com" onSetUser={vi.fn()} />);

    expect(screen.getByTestId("admin-users-table")).toBeInTheDocument();
    for (const u of USERS) {
      expect(screen.getByTestId(`admin-users-row-${u.email}`)).toBeInTheDocument();
    }
    expect(screen.getByTestId("admin-users-admin-admin@x.com")).toBeChecked();
    expect(screen.getByTestId("admin-users-admin-bob@x.com")).not.toBeChecked();
    expect(screen.getByTestId("admin-users-enabled-carol@x.com")).not.toBeChecked();
  });

  it("toggling admin / enabled dispatches onSetUser with just the changed flag", () => {
    const onSetUser = vi.fn();
    render(<AdminUsersPanel users={USERS} selfEmail="admin@x.com" onSetUser={onSetUser} />);

    fireEvent.click(screen.getByTestId("admin-users-admin-bob@x.com"));
    expect(onSetUser).toHaveBeenCalledWith({ email: "bob@x.com", is_admin: true });

    fireEvent.click(screen.getByTestId("admin-users-enabled-carol@x.com"));
    expect(onSetUser).toHaveBeenCalledWith({ email: "carol@x.com", enabled: true });
  });

  it("disables both toggles on the viewer's own row (backend self-guard mirror)", () => {
    const onSetUser = vi.fn();
    render(<AdminUsersPanel users={USERS} selfEmail="admin@x.com" onSetUser={onSetUser} />);

    const adminToggle = screen.getByTestId("admin-users-admin-admin@x.com");
    const enabledToggle = screen.getByTestId("admin-users-enabled-admin@x.com");
    expect(adminToggle).toBeDisabled();
    expect(enabledToggle).toBeDisabled();
    // A title explains WHY the toggles are inert.
    expect(adminToggle).toHaveAttribute("title", expect.stringMatching(/your own/i));
    expect(enabledToggle).toHaveAttribute("title", expect.stringMatching(/your own/i));

    fireEvent.click(adminToggle);
    expect(onSetUser).not.toHaveBeenCalled();

    // Other rows stay live.
    expect(screen.getByTestId("admin-users-admin-bob@x.com")).not.toBeDisabled();
  });

  it("shows an empty state when there are no users", () => {
    render(<AdminUsersPanel users={[]} selfEmail={null} onSetUser={vi.fn()} />);
    expect(screen.getByTestId("admin-users-empty")).toBeInTheDocument();
    expect(screen.queryByTestId("admin-users-table")).toBeNull();
  });
});
