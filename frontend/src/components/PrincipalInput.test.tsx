// PrincipalInput — proves the User/Role toggle emits the right principal string:
// a plain email in User mode, a `role:<key>` in Role mode, and that an incoming
// `role:` value opens directly in Role mode.

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { RoleView } from "../api";
import { PrincipalInput } from "./PrincipalInput";

const ROLES: RoleView[] = [
  { role: "pm", label: "PM", applicable: true, active: true, viewer_holds: false, viewer_has_open_application: false },
  { role: "qa", label: "QA", applicable: true, active: true, viewer_holds: false, viewer_has_open_application: false },
];

describe("PrincipalInput", () => {
  it("defaults to User mode and emits a raw email", () => {
    const onChange = vi.fn();
    render(<PrincipalInput value="" onChange={onChange} roles={ROLES} testid="p" ariaLabel="Owner" />);
    const input = screen.getByTestId("p");
    expect(screen.getByTestId("p-mode-user")).toHaveAttribute("aria-pressed", "true");
    fireEvent.change(input, { target: { value: "alice@x.com" } });
    expect(onChange).toHaveBeenCalledWith("alice@x.com");
  });

  it("switching to Role and picking a role emits role:<key>", () => {
    const onChange = vi.fn();
    render(<PrincipalInput value="" onChange={onChange} roles={ROLES} testid="p" ariaLabel="Owner" />);
    fireEvent.click(screen.getByTestId("p-mode-role"));
    const select = screen.getByTestId("p-role");
    fireEvent.change(select, { target: { value: "role:qa" } });
    expect(onChange).toHaveBeenCalledWith("role:qa");
  });

  it("opens in Role mode when the value is already a role principal", () => {
    render(<PrincipalInput value="role:pm" onChange={vi.fn()} roles={ROLES} testid="p" ariaLabel="Owner" />);
    expect(screen.getByTestId("p-mode-role")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("p-role")).toHaveValue("role:pm");
  });

  it("switching kind clears the value so a principal can't leak across modes", () => {
    const onChange = vi.fn();
    render(<PrincipalInput value="role:pm" onChange={onChange} roles={ROLES} testid="p" ariaLabel="Owner" />);
    fireEvent.click(screen.getByTestId("p-mode-user"));
    expect(onChange).toHaveBeenCalledWith("");
  });

  it("resolves getByLabelText to the User-mode input (keeps label association)", () => {
    render(<PrincipalInput value="" onChange={vi.fn()} roles={ROLES} testid="p" ariaLabel="Column owner" />);
    expect(screen.getByLabelText("Column owner")).toBe(screen.getByTestId("p"));
  });
});
