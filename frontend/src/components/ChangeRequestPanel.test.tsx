import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ChangeRequestPanel, type ChangeRequestView } from "./ChangeRequestPanel";

const cr: ChangeRequestView = {
  name: "CR1",
  requester: "E",
  resolved_approver: "C",
  status: "proposed",
};

describe("ChangeRequestPanel role-based controls (WEB_UI-088)", () => {
  it("approver C sees Approve/Reject, not Withdraw", () => {
    render(<ChangeRequestPanel cr={cr} viewer="C" onApprove={() => {}} onReject={() => {}} onWithdraw={() => {}} />);
    expect(screen.getByTestId("cr-approve")).toBeInTheDocument();
    expect(screen.getByTestId("cr-reject")).toBeInTheDocument();
    expect(screen.queryByTestId("cr-withdraw")).toBeNull();
  });

  it("requester E sees Withdraw, not Approve/Reject", () => {
    render(<ChangeRequestPanel cr={cr} viewer="E" onApprove={() => {}} onReject={() => {}} onWithdraw={() => {}} />);
    expect(screen.getByTestId("cr-withdraw")).toBeInTheDocument();
    expect(screen.queryByTestId("cr-approve")).toBeNull();
  });

  it("bystander F sees neither (read-only)", () => {
    render(<ChangeRequestPanel cr={cr} viewer="F" onApprove={() => {}} onReject={() => {}} onWithdraw={() => {}} />);
    expect(screen.getByTestId("cr-readonly")).toBeInTheDocument();
  });
});

describe("dense row + expandable detail (UX density)", () => {
  it("collapses the diff by default and reveals it on Details toggle", () => {
    render(<ChangeRequestPanel cr={cr} viewer="C" onApprove={() => {}} onReject={() => {}} onWithdraw={() => {}} />);
    // Collapsed: the one-line row summary is shown; the full cr-changes list is not.
    expect(screen.getByTestId("cr-rowsummary-CR1")).toBeInTheDocument();
    expect(screen.queryByTestId("cr-changes")).toBeNull();
    // Expanding reveals the full per-change breakdown.
    fireEvent.click(screen.getByTestId("cr-expand-CR1"));
    expect(screen.getByTestId("cr-changes")).toBeInTheDocument();
  });
});

describe("column-add CR lead prefers the human label (P2)", () => {
  it("renders the payload label, not the machine field key", () => {
    const colCr: ChangeRequestView = {
      name: "CR-COL",
      requester: "E",
      resolved_approver: "C",
      status: "proposed",
      operation: "add",
      target_kind: "column",
      payload: { field: "Ux_review_probe", label: "UX Review Probe" },
    };
    render(
      <ChangeRequestPanel cr={colCr} viewer="C" onApprove={() => {}} onReject={() => {}} onWithdraw={() => {}} />,
    );
    const lead = screen.getByTestId("cr-rowsummary-CR-COL");
    expect(lead).toHaveTextContent("UX Review Probe");
    expect(lead).not.toHaveTextContent("Ux_review_probe");
    expect(lead).not.toHaveTextContent("Ux review probe");
  });

  it("prefers patch.label for a column-update CR", () => {
    const updCr: ChangeRequestView = {
      name: "CR-UPD",
      requester: "E",
      resolved_approver: "C",
      status: "proposed",
      operation: "update",
      target_kind: "column",
      payload: { field: "Ux_review_probe", patch: { label: "UX Review Probe" } },
    };
    render(
      <ChangeRequestPanel cr={updCr} viewer="C" onApprove={() => {}} onReject={() => {}} onWithdraw={() => {}} />,
    );
    const lead = screen.getByTestId("cr-rowsummary-CR-UPD");
    expect(lead).toHaveTextContent("UX Review Probe");
    expect(lead).not.toHaveTextContent("Ux_review_probe");
  });

  it("falls back to the humanized field key when no label is present", () => {
    const colCr: ChangeRequestView = {
      name: "CR-NOLABEL",
      requester: "E",
      resolved_approver: "C",
      status: "proposed",
      operation: "add",
      target_kind: "column",
      payload: { field: "budget" },
    };
    render(
      <ChangeRequestPanel cr={colCr} viewer="C" onApprove={() => {}} onReject={() => {}} onWithdraw={() => {}} />,
    );
    expect(screen.getByTestId("cr-rowsummary-CR-NOLABEL")).toHaveTextContent("Budget");
  });
});

describe("approve idempotency (WEB_UI-089)", () => {
  it("double-clicking Approve dispatches approveChange once", () => {
    const onApprove = vi.fn();
    render(<ChangeRequestPanel cr={cr} viewer="C" onApprove={onApprove} onReject={() => {}} onWithdraw={() => {}} />);
    const btn = screen.getByTestId("cr-approve");
    fireEvent.click(btn);
    fireEvent.click(btn);
    expect(onApprove).toHaveBeenCalledTimes(1);
  });
});
