// Unit spec for RequestRoleControl (Feature: roles) — the user self-application
// header control. It must offer ONLY requestable roles (applicable && active &&
// !viewer_holds && !viewer_has_open_application) and dispatch applyForRole.

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { RequestRoleControl } from "./RequestRoleControl";
import type { RoleView } from "../api";

function role(over: Partial<RoleView>): RoleView {
  return {
    role: "pm",
    label: "PM",
    description: null,
    applicable: true,
    active: true,
    viewer_holds: false,
    viewer_has_open_application: false,
    ...over,
  };
}

describe("RequestRoleControl", () => {
  it("offers only requestable roles and dispatches applyForRole", () => {
    const onApply = vi.fn();
    render(
      <RequestRoleControl
        roles={[
          role({ role: "pm", label: "PM" }),
          role({ role: "marketing", label: "Marketing", applicable: false }), // not applicable
          role({ role: "developer", label: "Developer", viewer_holds: true }), // already held
          role({ role: "ops", label: "Ops", viewer_has_open_application: true }), // pending
          role({ role: "retired", label: "Retired", active: false }), // inactive
        ]}
        onApply={onApply}
      />,
    );
    const select = screen.getByTestId("request-role-select") as HTMLSelectElement;
    const optionValues = Array.from(select.options).map((o) => o.value);
    // Only PM is requestable; the empty placeholder + "pm".
    expect(optionValues).toEqual(["", "pm"]);

    fireEvent.change(select, { target: { value: "pm" } });
    fireEvent.change(screen.getByTestId("request-role-justification"), {
      target: { value: "I lead the roadmap" },
    });
    fireEvent.click(screen.getByTestId("request-role-submit"));
    expect(onApply).toHaveBeenCalledWith({ role: "pm", justification: "I lead the roadmap" });
  });

  it("shows held roles and renders nothing when there is neither held nor requestable", () => {
    const { rerender } = render(
      <RequestRoleControl roles={[role({ role: "pm", viewer_holds: true })]} onApply={vi.fn()} />,
    );
    expect(screen.getByTestId("held-pm")).toBeInTheDocument();

    rerender(
      <RequestRoleControl
        roles={[role({ role: "marketing", applicable: false })]}
        onApply={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("request-role-control")).toBeNull();
  });
});
