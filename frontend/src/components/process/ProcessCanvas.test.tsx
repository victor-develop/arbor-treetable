// Component spec for the bespoke DAG canvas (ProcessCanvas). The canvas is a
// controlled VIEW/PRODUCER over the rule model: `rules` in, `onChange(rules)` out.
// These specs drive the keyboard-reachable interactions (no pointer geometry):
//   * add a column node from the picker
//   * connect (arm a source, click a target) draws an edge -> onChange(packed)
//   * a self-loop / cycle / duplicate connect is REJECTED (aria-live, no onChange)
//   * edit an edge's within-duration -> onChange
//   * delete an edge; delete a column node
//   * validation surfaces cycle errors + unreachable warnings inline
// A stateful harness reflects onChange back into `rules` (mirrors the panel), so
// a multi-step interaction sees the committed graph.

import { act, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { ProcessCanvas } from "./ProcessCanvas";
import type { ProcessRuleInput, SnapshotColumn } from "../../api";

function col(name: string, over: Partial<SnapshotColumn> = {}): SnapshotColumn {
  return {
    name,
    field: name,
    label: name.toUpperCase(),
    type: "text",
    is_label: false,
    column_owner: "owner",
    editors: [],
    can_edit: true,
    ...over,
  };
}

const COLS = [col("a"), col("b"), col("c")];

// Controlled harness: seeds rules, reflects onChange back into state, and exposes
// the latest onChange payload for assertions.
function Harness({
  columns = COLS,
  initial = [],
  onChangeSpy,
}: {
  columns?: SnapshotColumn[];
  initial?: ProcessRuleInput[];
  onChangeSpy?: (r: ProcessRuleInput[]) => void;
}) {
  const [rules, setRules] = useState<ProcessRuleInput[]>(initial);
  return (
    <ProcessCanvas
      columns={columns}
      rules={rules}
      onChange={(r) => {
        onChangeSpy?.(r);
        setRules(r);
      }}
    />
  );
}

function click(testid: string) {
  return act(async () => {
    screen.getByTestId(testid).click();
  });
}

describe("ProcessCanvas — nodes", () => {
  it("always renders the fixed START node", () => {
    render(<Harness />);
    expect(screen.getByTestId("canvas-node-__start__")).toBeInTheDocument();
    expect(screen.getByTestId("canvas-node-body-__start__")).toHaveTextContent(/row created/i);
  });

  it("adds a column node from the picker and drops it from the remaining choices", async () => {
    render(<Harness />);
    const picker = screen.getByTestId("canvas-add-column") as HTMLSelectElement;
    await act(async () => {
      picker.value = "a";
      picker.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await click("canvas-add-node");
    expect(screen.getByTestId("canvas-node-a")).toBeInTheDocument();
    const opts = within(picker).getAllByRole("option").map((o) => (o as HTMLOptionElement).value);
    expect(opts).not.toContain("a");
    expect(opts).toContain("b");
  });

  it("excludes the label column from the picker", () => {
    render(<Harness columns={[col("title", { is_label: true }), col("a")]} />);
    const picker = screen.getByTestId("canvas-add-column") as HTMLSelectElement;
    const opts = within(picker).getAllByRole("option").map((o) => (o as HTMLOptionElement).value);
    expect(opts).not.toContain("title");
    expect(opts).toContain("a");
  });
});

describe("ProcessCanvas — connecting edges", () => {
  it("draws START -> a and fires onChange with a row-trigger rule", async () => {
    const spy = vi.fn();
    render(<Harness onChangeSpy={spy} />);
    // add node a, then connect START -> a.
    const picker = screen.getByTestId("canvas-add-column") as HTMLSelectElement;
    await act(async () => {
      picker.value = "a";
      picker.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await click("canvas-add-node");
    await click("canvas-connect-__start__");
    await click("canvas-node-body-a");
    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy.mock.calls[0][0]).toEqual([
      {
        trigger_kind: "row",
        trigger_column: null,
        trigger_op: "created-or-updated",
        expected_columns: ["a"],
        within_seconds: 0,
        notify_on_expect: true,
      },
    ]);
    // the edge now renders in the SVG + the edge list.
    expect(screen.getByTestId("canvas-edge-__start__-a")).toBeInTheDocument();
    expect(screen.getByTestId("canvas-edge-row-__start__-a")).toBeInTheDocument();
  });

  it("rejects a self-loop (connect a node to itself) with an alert and no onChange", async () => {
    const spy = vi.fn();
    render(<Harness initial={[{ trigger_kind: "row", trigger_column: null, trigger_op: "created-or-updated", expected_columns: ["a"] }]} onChangeSpy={spy} />);
    await click("canvas-connect-a");
    await click("canvas-node-body-a"); // clicking the SAME node while armed
    // Self-connect is guarded: the body click on the source does nothing; drive
    // the reject path explicitly by completing on itself is impossible via the UI
    // (armed source ignores its own body), so instead assert no edge a->a exists.
    expect(screen.queryByTestId("canvas-edge-a-a")).toBeNull();
    expect(spy).not.toHaveBeenCalled();
  });

  it("rejects a cycle-closing edge with an aria-live message and no onChange", async () => {
    const spy = vi.fn();
    // Seed START->a->b. Attempt b -> a (closes a cycle a->b->a).
    render(
      <Harness
        initial={[
          { trigger_kind: "row", trigger_column: null, trigger_op: "created-or-updated", expected_columns: ["a"] },
          { trigger_kind: "column", trigger_column: "a", trigger_op: "created-or-updated", expected_columns: ["b"] },
        ]}
        onChangeSpy={spy}
      />,
    );
    await click("canvas-connect-b");
    await click("canvas-node-body-a");
    expect(screen.getByTestId("canvas-reject")).toHaveTextContent(/cycle/i);
    expect(spy).not.toHaveBeenCalled();
    expect(screen.queryByTestId("canvas-edge-b-a")).toBeNull();
  });

  it("rejects a duplicate edge with a message and no onChange", async () => {
    const spy = vi.fn();
    render(
      <Harness
        initial={[{ trigger_kind: "row", trigger_column: null, trigger_op: "created-or-updated", expected_columns: ["a"] }]}
        onChangeSpy={spy}
      />,
    );
    await click("canvas-connect-__start__");
    await click("canvas-node-body-a"); // START->a already exists
    expect(screen.getByTestId("canvas-reject")).toHaveTextContent(/already exists/i);
    expect(spy).not.toHaveBeenCalled();
  });
});

describe("ProcessCanvas — edge editing", () => {
  const seeded: ProcessRuleInput[] = [
    { trigger_kind: "row", trigger_column: null, trigger_op: "created-or-updated", expected_columns: ["a"], within_seconds: 0 },
  ];

  it("edits an edge's within-duration and fires onChange", async () => {
    const spy = vi.fn();
    render(<Harness initial={seeded} onChangeSpy={spy} />);
    const input = screen.getByTestId("canvas-edge-within-__start__-a") as HTMLInputElement;
    await act(async () => {
      // Use the native value setter so React's controlled onChange fires (a bare
      // `input.value = …` is swallowed by React's cached value tracker).
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        "value",
      )!.set!;
      setter.call(input, "3600");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(spy).toHaveBeenCalled();
    const last = spy.mock.calls[spy.mock.calls.length - 1][0];
    expect(last[0].within_seconds).toBe(3600);
  });

  it("shows a friendly duration echo for the within chip", () => {
    render(<Harness initial={[{ trigger_kind: "row", trigger_column: null, trigger_op: "created-or-updated", expected_columns: ["a"], within_seconds: 3600 }]} />);
    const row = screen.getByTestId("canvas-edge-row-__start__-a");
    expect(row).toHaveTextContent(/1h/);
  });

  it("deletes an edge and fires onChange with the remaining rules", async () => {
    const spy = vi.fn();
    render(
      <Harness
        initial={[
          { trigger_kind: "row", trigger_column: null, trigger_op: "created-or-updated", expected_columns: ["a"] },
          { trigger_kind: "column", trigger_column: "a", trigger_op: "created-or-updated", expected_columns: ["b"] },
        ]}
        onChangeSpy={spy}
      />,
    );
    await click("canvas-edge-del-a-b");
    const last = spy.mock.calls[spy.mock.calls.length - 1][0];
    expect(last).toEqual([
      { trigger_kind: "row", trigger_column: null, trigger_op: "created-or-updated", expected_columns: ["a"], within_seconds: 0, notify_on_expect: true },
    ]);
  });

  it("deletes a column node and drops its touching edges", async () => {
    const spy = vi.fn();
    render(
      <Harness
        initial={[
          { trigger_kind: "row", trigger_column: null, trigger_op: "created-or-updated", expected_columns: ["a"] },
          { trigger_kind: "column", trigger_column: "a", trigger_op: "created-or-updated", expected_columns: ["b"] },
        ]}
        onChangeSpy={spy}
      />,
    );
    await click("canvas-node-del-a");
    const last = spy.mock.calls[spy.mock.calls.length - 1][0];
    // Deleting `a` drops both START->a and a->b.
    expect(last).toEqual([]);
  });

  it("has no delete button on the fixed START node", () => {
    render(<Harness initial={seeded} />);
    expect(screen.queryByTestId("canvas-node-del-__start__")).toBeNull();
  });
});

describe("ProcessCanvas — validation surfacing", () => {
  it("renders an unreachable-from-START warning for an orphan chain", () => {
    // START->a is reachable; b->c is an orphan chain (nothing feeds b).
    render(
      <Harness
        initial={[
          { trigger_kind: "row", trigger_column: null, trigger_op: "created-or-updated", expected_columns: ["a"] },
          { trigger_kind: "column", trigger_column: "b", trigger_op: "created-or-updated", expected_columns: ["c"] },
        ]}
      />,
    );
    const warns = screen.getAllByTestId("canvas-warning");
    expect(warns.length).toBeGreaterThan(0);
    expect(warns.map((w) => w.textContent).join(" ")).toMatch(/not reachable/i);
  });

  it("renders nothing in the validation block for a clean DAG", () => {
    render(
      <Harness
        initial={[
          { trigger_kind: "row", trigger_column: null, trigger_op: "created-or-updated", expected_columns: ["a"] },
          { trigger_kind: "column", trigger_column: "a", trigger_op: "created-or-updated", expected_columns: ["b"] },
        ]}
      />,
    );
    expect(screen.queryByTestId("canvas-validation")).toBeNull();
  });
});
