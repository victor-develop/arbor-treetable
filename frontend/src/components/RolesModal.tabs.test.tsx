// Admin modal tabs (platform-admin console). The header-launched Roles modal is
// now a tabbed "Admin" surface: Roles (the existing RolesAdminPanel), Catalog
// (RoleCatalogPanel over arbor.admin.create_role/update_role), and Users
// (AdminUsersPanel over arbor.admin.list_users/set_user). These specs drive App
// at the integration boundary — mirroring RolesModal.test.tsx — asserting tab
// switching, the lazy user load on open, and that the Catalog/Users writes call
// the standalone client methods then refresh the affected view.

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import App from "../App";
import { loginAs, mockClient } from "../test/fixture";
import type { ArborClient, RoleView, UserRow } from "../api";

const ROLES: RoleView[] = [
  { role: "pm", label: "PM", applicable: true, active: true, viewer_holds: false, viewer_has_open_application: false },
];
const USERS: UserRow[] = [
  // "A" is the viewer (snapshot actor) — its row must be self-guarded.
  { email: "A", full_name: "Admin A", is_admin: true, enabled: true, creation: "2026-01-01" },
  { email: "bob@x.com", full_name: "Bob", is_admin: false, enabled: true, creation: "2026-02-01" },
];

// Admin client with the standalone arbor.admin.* face mocked in (spies returned
// alongside so specs can assert the create/update/set calls + refreshes).
function adminClient() {
  const base = mockClient({ snapshot: loginAs("A", { viewer: { can_add_column: true, is_admin: true } }) });
  const listUsers = vi.fn(async () => USERS);
  const createRole = vi.fn(async (p: { role: string; label: string }) => ({
    ...p, applicable: true, active: true, viewer_holds: false, viewer_has_open_application: false,
  }) as RoleView);
  const updateRole = vi.fn(async () => ROLES[0]);
  const setUser = vi.fn(async () => USERS[1]);
  const listRoles = vi.fn(async () => ROLES);
  const client: ArborClient = {
    ...base.client,
    listRoles,
    listRoleApplications: async () => [],
    listRoleGrants: async () => [],
    listUsers,
    createRole,
    updateRole,
    setUser,
  };
  return { ...base, client, listUsers, createRole, updateRole, setUser, listRoles };
}

async function openModal() {
  await screen.findByTestId("tree-table");
  fireEvent.click(await screen.findByTestId("roles-admin-button"));
  return screen.findByTestId("roles-modal");
}

describe("Admin modal — tab strip", () => {
  it("renders an Admin header with three tabs, Roles active by default", async () => {
    const { client } = adminClient();
    render(<App client={client} sheetName="S" />);
    const modal = await openModal();

    expect(within(modal).getByText("Admin")).toBeInTheDocument();
    expect(within(modal).getByTestId("admin-tab-roles")).toHaveAttribute("aria-selected", "true");
    expect(within(modal).getByTestId("admin-tab-catalog")).toHaveAttribute("aria-selected", "false");
    expect(within(modal).getByTestId("admin-tab-users")).toHaveAttribute("aria-selected", "false");
    // Roles tab content (the existing panel) is mounted; the others are not.
    expect(within(modal).getByTestId("roles-panel")).toBeInTheDocument();
    expect(within(modal).queryByTestId("role-catalog-panel")).toBeNull();
    expect(within(modal).queryByTestId("admin-users-panel")).toBeNull();
  });

  it("switches between Roles / Catalog / Users tabs", async () => {
    const { client } = adminClient();
    render(<App client={client} sheetName="S" />);
    const modal = await openModal();

    fireEvent.click(within(modal).getByTestId("admin-tab-catalog"));
    expect(within(modal).getByTestId("role-catalog-panel")).toBeInTheDocument();
    expect(within(modal).queryByTestId("roles-panel")).toBeNull();

    fireEvent.click(within(modal).getByTestId("admin-tab-users"));
    expect(within(modal).getByTestId("admin-users-panel")).toBeInTheDocument();
    expect(within(modal).queryByTestId("role-catalog-panel")).toBeNull();

    fireEvent.click(within(modal).getByTestId("admin-tab-roles"));
    expect(within(modal).getByTestId("roles-panel")).toBeInTheDocument();
  });

  it("loads users when the modal opens and renders them on the Users tab, self-guarded", async () => {
    const { client, listUsers } = adminClient();
    render(<App client={client} sheetName="S" />);
    const modal = await openModal();
    await waitFor(() => expect(listUsers).toHaveBeenCalled());

    fireEvent.click(within(modal).getByTestId("admin-tab-users"));
    await within(modal).findByTestId("admin-users-row-bob@x.com");
    // The viewer's own row ("A", the snapshot actor) mirrors the backend self-guard.
    expect(within(modal).getByTestId("admin-users-admin-A")).toBeDisabled();
    expect(within(modal).getByTestId("admin-users-admin-bob@x.com")).not.toBeDisabled();
  });

  it("Users tab toggle calls client.setUser then refreshes the user list", async () => {
    const { client, setUser, listUsers } = adminClient();
    render(<App client={client} sheetName="S" />);
    const modal = await openModal();

    fireEvent.click(within(modal).getByTestId("admin-tab-users"));
    fireEvent.click(await within(modal).findByTestId("admin-users-admin-bob@x.com"));
    await waitFor(() =>
      expect(setUser).toHaveBeenCalledWith({ email: "bob@x.com", is_admin: true }),
    );
    // The list is re-fetched after the write (open-load + refresh = 2+ calls).
    await waitFor(() => expect(listUsers.mock.calls.length).toBeGreaterThanOrEqual(2));
  });

  it("Catalog create calls client.createRole then refreshes the role catalog", async () => {
    const { client, createRole, listRoles } = adminClient();
    render(<App client={client} sheetName="S" />);
    const modal = await openModal();
    const before = listRoles.mock.calls.length;

    fireEvent.click(within(modal).getByTestId("admin-tab-catalog"));
    fireEvent.change(within(modal).getByTestId("role-catalog-key"), { target: { value: "qa" } });
    fireEvent.change(within(modal).getByTestId("role-catalog-label"), { target: { value: "QA" } });
    fireEvent.click(within(modal).getByTestId("role-catalog-create-submit"));

    await waitFor(() =>
      expect(createRole).toHaveBeenCalledWith({ role: "qa", label: "QA", applicable: true }),
    );
    await waitFor(() => expect(listRoles.mock.calls.length).toBeGreaterThan(before));
  });

  it("Catalog edit calls client.updateRole", async () => {
    const { client, updateRole } = adminClient();
    render(<App client={client} sheetName="S" />);
    const modal = await openModal();

    fireEvent.click(within(modal).getByTestId("admin-tab-catalog"));
    fireEvent.click(within(modal).getByTestId("role-catalog-edit-pm"));
    fireEvent.click(within(modal).getByTestId("role-catalog-edit-active")); // deactivate
    fireEvent.click(within(modal).getByTestId("role-catalog-edit-save"));

    await waitFor(() =>
      expect(updateRole).toHaveBeenCalledWith({
        role: "pm",
        label: "PM",
        applicable: true,
        active: false,
      }),
    );
  });
});
