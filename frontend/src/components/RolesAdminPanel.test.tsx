// Unit spec for RolesAdminPanel (Feature: roles). Admin: assign / revoke /
// approve / reject. Non-admin: read-only own applications with withdraw.

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { RolesAdminPanel } from "./RolesAdminPanel";
import type { RoleApplicationView, RoleGrantView, RoleView } from "../api";

const ROLES: RoleView[] = [
  { role: "pm", label: "PM", applicable: true, active: true, viewer_holds: false, viewer_has_open_application: false },
  { role: "developer", label: "Developer", applicable: true, active: true, viewer_holds: false, viewer_has_open_application: false },
];
const GRANT: RoleGrantView = {
  name: "g1", role: "pm", grantee: "alice@x.com", granted_by: "admin@x.com", source: "admin-grant", can_revoke: true,
};
const APP: RoleApplicationView = {
  name: "a1", role: "developer", requester: "bob@x.com", status: "proposed", justification: "I build", viewer_is_approver: true,
};

function handlers() {
  return { onAssign: vi.fn(), onRevoke: vi.fn(), onApprove: vi.fn(), onReject: vi.fn(), onWithdraw: vi.fn() };
}

describe("RolesAdminPanel — admin", () => {
  it("assigns a role, revokes a grant, and approves/rejects applications", () => {
    const h = handlers();
    render(
      <RolesAdminPanel isAdmin roles={ROLES} grants={[GRANT]} applications={[APP]} {...h} />,
    );

    // assign
    fireEvent.change(screen.getByTestId("assign-role-select"), { target: { value: "pm" } });
    fireEvent.change(screen.getByTestId("assign-role-grantee"), { target: { value: "carol@x.com" } });
    fireEvent.click(screen.getByTestId("assign-role-submit"));
    expect(h.onAssign).toHaveBeenCalledWith({ role: "pm", grantee: "carol@x.com" });

    // revoke (by role+grantee, not grant name — matches revokeRole params)
    fireEvent.click(screen.getByTestId("role-grant-revoke-g1"));
    expect(h.onRevoke).toHaveBeenCalledWith({ role: "pm", grantee: "alice@x.com" });

    // approve / reject the pending application
    fireEvent.click(screen.getByTestId("role-app-approve-a1"));
    expect(h.onApprove).toHaveBeenCalledWith({ role_application: "a1" });
    fireEvent.click(screen.getByTestId("role-app-reject-a1"));
    expect(h.onReject).toHaveBeenCalledWith({ role_application: "a1" });
  });
});

describe("RolesAdminPanel — non-admin", () => {
  it("shows own applications read-only and withdraws an open one; no assign form", () => {
    const h = handlers();
    const mine: RoleApplicationView = { ...APP, requester: "me@x.com", viewer_is_approver: false };
    render(
      <RolesAdminPanel isAdmin={false} roles={ROLES} grants={[]} applications={[mine]} {...h} />,
    );
    expect(screen.queryByTestId("assign-role-form")).toBeNull();
    fireEvent.click(screen.getByTestId("role-app-withdraw-a1"));
    expect(h.onWithdraw).toHaveBeenCalledWith({ role_application: "a1" });
  });
});
