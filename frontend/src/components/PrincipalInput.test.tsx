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

describe("PrincipalInput — Domain mode", () => {
  it("offers no Domain tab unless the slot allows it", () => {
    // Owner/editor slots must not offer it: the server rejects a domain there,
    // and a control that always errors is worse than no control.
    render(<PrincipalInput value="" onChange={vi.fn()} testid="p" />);
    expect(screen.queryByTestId("p-mode-domain")).toBeNull();
  });

  it("emits the domain: shape from a bare host", () => {
    // The box shows only the host, so nobody has to know the prefix convention
    // (or can half-type it).
    const onChange = vi.fn();
    render(<PrincipalInput value="" onChange={onChange} testid="p" allowDomain />);
    fireEvent.click(screen.getByTestId("p-mode-domain"));
    fireEvent.change(screen.getByTestId("p-domain"), { target: { value: "example.com" } });
    expect(onChange).toHaveBeenCalledWith("domain:example.com");
  });

  it("shows an existing domain value as its host, in Domain mode", () => {
    render(<PrincipalInput value="domain:example.com" onChange={vi.fn()} testid="p" allowDomain />);
    expect(screen.getByTestId("p-mode-domain").getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByTestId("p-domain")).toHaveValue("example.com");
  });

  it("clears the value when switching kind, both directions", () => {
    // A stale email must not leak into a domain slot (or a host into a user one).
    const onChange = vi.fn();
    const { rerender } = render(
      <PrincipalInput value="a@x.com" onChange={onChange} testid="p" allowDomain />,
    );
    fireEvent.click(screen.getByTestId("p-mode-domain"));
    expect(onChange).toHaveBeenCalledWith("");

    onChange.mockClear();
    rerender(<PrincipalInput value="domain:x.com" onChange={onChange} testid="p" allowDomain />);
    fireEvent.click(screen.getByTestId("p-mode-user"));
    expect(onChange).toHaveBeenCalledWith("");
  });

  it("empties the value when the host is cleared", () => {
    const onChange = vi.fn();
    render(<PrincipalInput value="domain:x.com" onChange={onChange} testid="p" allowDomain />);
    fireEvent.change(screen.getByTestId("p-domain"), { target: { value: "  " } });
    expect(onChange).toHaveBeenCalledWith("");
  });
});
