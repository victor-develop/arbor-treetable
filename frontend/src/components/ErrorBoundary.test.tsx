// ErrorBoundary — proves it confines a descendant's render crash to its own
// subtree (a sibling outside the boundary keeps rendering), surfaces a labelled
// recoverable fallback, and clears the error both via its reset button and via a
// resetKeys change (modal re-open / sheet switch). React logs caught errors to
// console.error; we suppress that expected noise per-test and restore it after.

import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ErrorBoundary } from "./ErrorBoundary";

// A child that throws during render iff `boom` is true — lets a single component
// model both the crashing and the recovered state.
function Boom({ boom }: { boom: boolean }): JSX.Element {
  if (boom) throw new Error("kaboom");
  return <span data-testid="ok">alive</span>;
}

describe("ErrorBoundary", () => {
  beforeEach(() => {
    // Suppress React's expected error logging (and our componentDidCatch log).
    vi.spyOn(console, "error").mockImplementation(() => {});
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders children normally when nothing throws", () => {
    render(
      <ErrorBoundary label="panel">
        <Boom boom={false} />
      </ErrorBoundary>,
    );
    expect(screen.getByTestId("ok")).toHaveTextContent("alive");
    expect(screen.queryByTestId("error-boundary-panel")).toBeNull();
  });

  it("shows the labelled fallback on a child render error while a sibling outside the boundary still renders", () => {
    render(
      <div>
        <ErrorBoundary label="panel">
          <Boom boom={true} />
        </ErrorBoundary>
        <span data-testid="sibling">still here</span>
      </div>,
    );
    // Containment: the boundary's fallback appears...
    expect(screen.getByTestId("error-boundary-panel")).toBeInTheDocument();
    expect(screen.getByTestId("error-boundary-panel-reset")).toBeInTheDocument();
    // ...and the sibling outside the boundary is untouched.
    expect(screen.getByTestId("sibling")).toHaveTextContent("still here");
  });

  it("recovers when the reset button is clicked after the throwing condition is fixed", () => {
    const { rerender } = render(
      <ErrorBoundary label="panel">
        <Boom boom={true} />
      </ErrorBoundary>,
    );
    expect(screen.getByTestId("error-boundary-panel")).toBeInTheDocument();

    // Fix the throwing condition, THEN click reset — the boundary re-renders the
    // now-healthy child.
    rerender(
      <ErrorBoundary label="panel">
        <Boom boom={false} />
      </ErrorBoundary>,
    );
    fireEvent.click(screen.getByTestId("error-boundary-panel-reset"));

    expect(screen.queryByTestId("error-boundary-panel")).toBeNull();
    expect(screen.getByTestId("ok")).toHaveTextContent("alive");
  });

  it("auto-clears the error when a resetKey changes", () => {
    const { rerender } = render(
      <ErrorBoundary label="panel" resetKeys={["a"]}>
        <Boom boom={true} />
      </ErrorBoundary>,
    );
    expect(screen.getByTestId("error-boundary-panel")).toBeInTheDocument();

    // Fix the child AND bump the resetKey — the boundary clears without a manual click.
    rerender(
      <ErrorBoundary label="panel" resetKeys={["b"]}>
        <Boom boom={false} />
      </ErrorBoundary>,
    );

    expect(screen.queryByTestId("error-boundary-panel")).toBeNull();
    expect(screen.getByTestId("ok")).toHaveTextContent("alive");
  });
});
