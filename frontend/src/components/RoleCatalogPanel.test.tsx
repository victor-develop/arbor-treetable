// Unit spec for RoleCatalogPanel (Admin modal — Catalog tab). Lists the role
// definitions, creates a new role (key/label/description/applicable), and
// edits an existing one (label/applicable/active) via onUpdateRole.

import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { RoleCatalogPanel } from "./RoleCatalogPanel";
import type { RoleView } from "../api";

const ROLES: RoleView[] = [
  { role: "pm", label: "PM", applicable: true, active: true, viewer_holds: false, viewer_has_open_application: false },
  { role: "auditor", label: "Auditor", applicable: false, active: false, viewer_holds: false, viewer_has_open_application: false },
];

function handlers() {
  return { onCreateRole: vi.fn(), onUpdateRole: vi.fn() };
}

describe("RoleCatalogPanel", () => {
  it("lists every role with its key + inactive/not-applicable markers", () => {
    const h = handlers();
    render(<RoleCatalogPanel roles={ROLES} {...h} />);

    expect(screen.getByTestId("role-catalog-list")).toBeInTheDocument();
    expect(screen.getByTestId("role-catalog-row-pm")).toHaveTextContent("PM");
    const auditor = screen.getByTestId("role-catalog-row-auditor");
    expect(auditor).toHaveTextContent(/inactive/i);
    expect(auditor).toHaveTextContent(/not self-applicable/i);
  });

  it("creates a role from the form and resets it (Create disabled until key+label)", () => {
    const h = handlers();
    render(<RoleCatalogPanel roles={ROLES} {...h} />);

    const submit = screen.getByTestId("role-catalog-create-submit");
    expect(submit).toBeDisabled();

    fireEvent.change(screen.getByTestId("role-catalog-key"), { target: { value: "qa" } });
    expect(submit).toBeDisabled(); // label still empty
    fireEvent.change(screen.getByTestId("role-catalog-label"), { target: { value: "QA" } });
    fireEvent.change(screen.getByTestId("role-catalog-description"), { target: { value: "Tests things" } });
    fireEvent.click(screen.getByTestId("role-catalog-applicable")); // true -> false
    fireEvent.click(submit);

    expect(h.onCreateRole).toHaveBeenCalledWith({
      role: "qa",
      label: "QA",
      description: "Tests things",
      applicable: false,
    });
    // Form resets after submit.
    expect(screen.getByTestId("role-catalog-key")).toHaveValue("");
    expect(screen.getByTestId("role-catalog-label")).toHaveValue("");
  });

  it("omits an empty description from the create params", () => {
    const h = handlers();
    render(<RoleCatalogPanel roles={[]} {...h} />);

    fireEvent.change(screen.getByTestId("role-catalog-key"), { target: { value: "qa" } });
    fireEvent.change(screen.getByTestId("role-catalog-label"), { target: { value: "QA" } });
    fireEvent.click(screen.getByTestId("role-catalog-create-submit"));

    expect(h.onCreateRole).toHaveBeenCalledWith({ role: "qa", label: "QA", applicable: true });
  });

  it("edits a role inline (label/applicable/active) and dispatches onUpdateRole", () => {
    const h = handlers();
    render(<RoleCatalogPanel roles={ROLES} {...h} />);

    fireEvent.click(screen.getByTestId("role-catalog-edit-pm"));
    const form = screen.getByTestId("role-catalog-edit-form");
    // Seeds from the current role.
    expect(within(form).getByTestId("role-catalog-edit-label")).toHaveValue("PM");
    expect(within(form).getByTestId("role-catalog-edit-applicable")).toBeChecked();
    expect(within(form).getByTestId("role-catalog-edit-active")).toBeChecked();

    fireEvent.change(within(form).getByTestId("role-catalog-edit-label"), {
      target: { value: "Product Manager" },
    });
    fireEvent.click(within(form).getByTestId("role-catalog-edit-active")); // true -> false
    fireEvent.click(within(form).getByTestId("role-catalog-edit-save"));

    expect(h.onUpdateRole).toHaveBeenCalledWith({
      role: "pm",
      label: "Product Manager",
      applicable: true,
      active: false,
    });
    // The editor closes after save.
    expect(screen.queryByTestId("role-catalog-edit-form")).toBeNull();
  });

  it("shows an empty state when the catalog is empty", () => {
    const h = handlers();
    render(<RoleCatalogPanel roles={[]} {...h} />);
    expect(screen.getByTestId("role-catalog-empty")).toBeInTheDocument();
    expect(screen.queryByTestId("role-catalog-list")).toBeNull();
  });
});
