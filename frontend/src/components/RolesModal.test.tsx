// Global Roles admin modal (IA fix). Role data is site-wide, so role
// administration is NOT a per-sheet Governance tab anymore — it launches from an
// admin-only header button into a modal. These specs drive App at the integration
// boundary: an admin sees the header "Roles" button (with a pending-applications
// count badge), clicking it opens the modal showing the applications inbox +
// grants + assign form, and Approve/Reject dispatch through executeAction. A
// non-admin viewer sees no header Roles button at all.

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import App from "../App";
import { loginAs, mockClient } from "../test/fixture";
import type {
  ArborClient,
  RoleApplicationView,
  RoleGrantView,
  RoleView,
} from "../api";

const ROLES: RoleView[] = [
  { role: "pm", label: "PM", applicable: true, active: true, viewer_holds: false, viewer_has_open_application: false },
  { role: "developer", label: "Developer", applicable: true, active: true, viewer_holds: false, viewer_has_open_application: false },
];
const GRANT: RoleGrantView = {
  name: "g1", role: "pm", grantee: "alice@x.com", granted_by: "admin@x.com", source: "admin-grant", can_revoke: true,
};
const APPS: RoleApplicationView[] = [
  { name: "a1", role: "developer", requester: "bob@x.com", status: "proposed", justification: "I build", viewer_is_approver: true },
  { name: "a2", role: "pm", requester: "carol@x.com", status: "proposed", viewer_is_approver: true },
];

// Extend the base mock client with the site-wide role list endpoints. is_admin is
// set on the snapshot viewer so App treats this session as an admin.
function adminClient() {
  const base = mockClient({ snapshot: loginAs("A", { viewer: { can_add_column: true, is_admin: true } }) });
  const client: ArborClient = {
    ...base.client,
    listRoles: async () => ROLES,
    listRoleApplications: async () => APPS,
    listRoleGrants: async () => [GRANT],
  };
  return { ...base, client };
}

describe("RolesModal — global header-launched admin Roles modal", () => {
  it("shows an admin-only header Roles button with a pending-count badge", async () => {
    const { client } = adminClient();
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");

    const btn = await screen.findByTestId("roles-admin-button");
    // 2 proposed applications -> badge "2".
    await waitFor(() => expect(btn.querySelector(".arbor-count")?.textContent?.trim()).toBe("2"));
    expect(btn).toHaveTextContent(/roles/i);

    // Modal is not mounted until the button is clicked.
    expect(screen.queryByTestId("roles-modal")).toBeNull();
  });

  it("opening the modal shows the applications inbox, grants, and assign form", async () => {
    const { client } = adminClient();
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");

    fireEvent.click(await screen.findByTestId("roles-admin-button"));
    const modal = await screen.findByTestId("roles-modal");
    expect(within(modal).getByTestId("roles-panel")).toBeInTheDocument();
    expect(within(modal).getByTestId("role-applications")).toBeInTheDocument();
    expect(within(modal).getByTestId("role-grants")).toBeInTheDocument();
    expect(within(modal).getByTestId("assign-role-form")).toBeInTheDocument();
  });

  it("Approve / Reject inside the modal dispatch through executeAction", async () => {
    const { client, calls } = adminClient();
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");

    fireEvent.click(await screen.findByTestId("roles-admin-button"));
    await screen.findByTestId("roles-modal");

    fireEvent.click(screen.getByTestId("role-app-approve-a1"));
    await waitFor(() =>
      expect(calls).toContainEqual({ action: "approveRoleApplication", params: { role_application: "a1" } }),
    );

    fireEvent.click(screen.getByTestId("role-app-reject-a2"));
    await waitFor(() =>
      expect(calls).toContainEqual({ action: "rejectRoleApplication", params: { role_application: "a2" } }),
    );
  });

  it("the close button dismisses the modal", async () => {
    const { client } = adminClient();
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");

    fireEvent.click(await screen.findByTestId("roles-admin-button"));
    await screen.findByTestId("roles-modal");
    fireEvent.click(screen.getByTestId("roles-modal-close"));
    expect(screen.queryByTestId("roles-modal")).toBeNull();
  });

  it("a non-admin viewer sees no header Roles button", async () => {
    // No is_admin flag -> not an admin. The button is gated on snap.viewer.is_admin.
    const base = mockClient({ snapshot: loginAs("A") });
    const client: ArborClient = {
      ...base.client,
      listRoles: async () => ROLES,
      listRoleApplications: async () => [],
      listRoleGrants: async () => [],
    };
    render(<App client={client} sheetName="S" />);
    await screen.findByTestId("tree-table");
    expect(screen.queryByTestId("roles-admin-button")).toBeNull();
  });
});
