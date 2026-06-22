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
