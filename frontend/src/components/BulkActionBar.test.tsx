// RED spec for P2 bulk CR triage (not yet implemented). These tests pin down the
// intended App API surface for BulkActionBar + the authority-scoped selection
// helper so the implementation can be written against a fixed contract.
//
// Intended App wiring (mirrored here):
//   - A selection helper `selectableCRs(crs)` (or hook) returns ONLY the CR names
//     whose viewer_is_approver === true. "Select all I can approve" feeds from
//     this, so a read-only CR can never enter the selection set.
//   - <BulkActionBar selected={string[]} onApprove={(name)=>Promise}
//     onReject={(name, reason?)=>Promise} onClear={()=>void} /> renders the
//     sticky bar, loops the per-CR async fn over `selected` (independent calls),
//     and reports an aggregate "X approved · Y failed" summary with [Retry failed]
//     that re-runs ONLY the failed ids.
//
// Import paths are the intended ones; the module does not exist yet → RED.

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { BulkActionBar, selectableCRs } from "./BulkActionBar";
import type { ChangeRequestView } from "../api";

function cr(name: string, viewer_is_approver: boolean): ChangeRequestView {
  return {
    name,
    requester: "E",
    resolved_approver: viewer_is_approver ? "C" : "D",
    status: "proposed",
    viewer_is_approver,
  };
}

describe("BulkActionBar — selected bar controls", () => {
  it('shows "N selected", Approve N, Reject N, and Clear', () => {
    render(
      <BulkActionBar
        selected={["CR1", "CR2", "CR3"]}
        onApprove={vi.fn().mockResolvedValue(undefined)}
        onReject={vi.fn().mockResolvedValue(undefined)}
        onClear={vi.fn()}
      />,
    );
    const bar = screen.getByTestId("cr-bulk-bar");
    expect(bar).toHaveTextContent("3 selected");
    expect(screen.getByTestId("cr-bulk-approve")).toHaveTextContent("Approve 3");
    expect(screen.getByTestId("cr-bulk-reject")).toHaveTextContent("Reject 3");
    // Clear control present.
    expect(bar).toHaveTextContent("Clear");
  });

  it("renders nothing when the selection is empty", () => {
    render(
      <BulkActionBar
        selected={[]}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onClear={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("cr-bulk-bar")).toBeNull();
  });

  it("Clear invokes onClear", () => {
    const onClear = vi.fn();
    render(
      <BulkActionBar
        selected={["CR1"]}
        onApprove={vi.fn().mockResolvedValue(undefined)}
        onReject={vi.fn().mockResolvedValue(undefined)}
        onClear={onClear}
      />,
    );
    fireEvent.click(screen.getByText("Clear"));
    expect(onClear).toHaveBeenCalledTimes(1);
  });
});

describe("authority-scoped selection (select all I can approve)", () => {
  const crs: ChangeRequestView[] = [
    cr("CR1", true),
    cr("CR2", false), // read-only for viewer
    cr("CR3", true),
    cr("CR4", false), // read-only for viewer
  ];

  it("selectableCRs returns only CRs where viewer_is_approver is true", () => {
    expect(selectableCRs(crs)).toEqual(["CR1", "CR3"]);
  });

  it("never includes a read-only CR", () => {
    const result = selectableCRs(crs);
    expect(result).not.toContain("CR2");
    expect(result).not.toContain("CR4");
  });

  it("treats a missing viewer_is_approver flag as not selectable", () => {
    const ambiguous: ChangeRequestView = {
      name: "CRX",
      requester: "E",
      resolved_approver: "C",
      status: "proposed",
      // viewer_is_approver intentionally omitted
    };
    expect(selectableCRs([ambiguous])).toEqual([]);
  });
});

describe("bulk reject — shared reason field", () => {
  it("offers a single shared optional reason field before confirming", async () => {
    const onReject = vi.fn().mockResolvedValue(undefined);
    render(
      <BulkActionBar
        selected={["CR1", "CR2"]}
        onApprove={vi.fn().mockResolvedValue(undefined)}
        onReject={onReject}
        onClear={vi.fn()}
      />,
    );

    // Clicking Reject N opens the shared reason field (does not fire per-CR yet).
    fireEvent.click(screen.getByTestId("cr-bulk-reject"));
    const reason = screen.getByTestId("cr-bulk-reject-reason");
    expect(reason).toBeInTheDocument();
    expect(onReject).not.toHaveBeenCalled();

    fireEvent.change(reason, { target: { value: "duplicate of CR0" } });

    // Confirming loops onReject(name, reason) once per selected CR id.
    fireEvent.click(screen.getByTestId("cr-bulk-reject"));
    await waitFor(() => expect(onReject).toHaveBeenCalledTimes(2));
    expect(onReject).toHaveBeenCalledWith("CR1", "duplicate of CR0");
    expect(onReject).toHaveBeenCalledWith("CR2", "duplicate of CR0");
  });
});

describe("partial-failure summary + retry", () => {
  it('reports "X approved · Y failed" and retry re-runs only the failed ids', async () => {
    // Mock approve that fails for CR2 only.
    const onApprove = vi.fn((name: string) =>
      name === "CR2" ? Promise.reject(new Error("boom")) : Promise.resolve(undefined),
    );

    render(
      <BulkActionBar
        selected={["CR1", "CR2", "CR3"]}
        onApprove={onApprove}
        onReject={vi.fn().mockResolvedValue(undefined)}
        onClear={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByTestId("cr-bulk-approve"));

    // One consolidated summary line — no per-call toast spam. Copy reads
    // "X approved of N · Y failed" so the result state names the attempted total.
    const summary = await screen.findByTestId("cr-bulk-summary");
    expect(summary).toHaveTextContent("2 approved");
    expect(summary).toHaveTextContent("of 3");
    expect(summary).toHaveTextContent("1 failed");

    // First pass attempted all three.
    expect(onApprove).toHaveBeenCalledTimes(3);

    // Retry re-runs ONLY the failed id (CR2).
    onApprove.mockClear();
    fireEvent.click(screen.getByTestId("cr-bulk-retry"));
    await waitFor(() => expect(onApprove).toHaveBeenCalledTimes(1));
    expect(onApprove).toHaveBeenCalledWith("CR2");
  });
});
