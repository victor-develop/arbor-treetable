// ErrorBoundary — isolates render crashes so one panel's failure never unmounts
// #root (a thrown error during render, absent a boundary, tears down the whole
// React tree and leaves the user with a blank white screen). Wrapping a panel /
// modal / sheet in this boundary confines the blast radius to that subtree: the
// rest of the app keeps rendering, and the failed region degrades to a small
// recoverable fallback card instead of taking everything with it.
//
// This MUST be a class component — only class components implement the
// getDerivedStateFromError / componentDidCatch lifecycle that React uses to catch
// render-phase errors; hooks cannot.

import { Component } from "react";
import type { ErrorInfo, ReactNode } from "react";

type Fallback = ReactNode | ((err: Error, reset: () => void) => ReactNode);

interface ErrorBoundaryProps {
  // Drives the fallback's data-testid (`error-boundary-${label}`) and its reset
  // button's data-testid (`error-boundary-${label}-reset`). Also keeps multiple
  // boundaries on one screen individually addressable in tests / telemetry.
  label: string;
  children: ReactNode;
  // Custom fallback: either a static node, or a render function handed the caught
  // error plus a `reset` callback (so a caller can build its own retry affordance).
  // Defaults to the inline "arbor-banner is-error" card below.
  fallback?: Fallback;
  // Invoked alongside the internal state reset (e.g. to refetch / clear caller state).
  onReset?: () => void;
  // When any entry changes between renders, the boundary auto-clears its error —
  // so re-opening a modal or switching sheets recovers without a manual retry.
  resetKeys?: unknown[];
}

interface ErrorBoundaryState {
  error: Error | null;
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  // React calls this after a descendant throws during render; the returned partial
  // state flips us into the fallback branch.
  static getDerivedStateFromError(err: Error): ErrorBoundaryState {
    return { error: err };
  }

  componentDidCatch(err: Error, info: ErrorInfo): void {
    // A telemetry reporter (Sentry / Datadog / etc.) would hook in HERE — this repo
    // ships no telemetry client, so we log to the console for now.
    console.error(`[ErrorBoundary:${this.props.label}]`, err, info.componentStack);
  }

  // Detect a change in any resetKey and auto-clear the captured error, so a
  // remount-worthy prop change (modal re-open, sheet switch) recovers on its own.
  componentDidUpdate(prev: ErrorBoundaryProps): void {
    if (this.state.error === null) return;
    if (keysChanged(prev.resetKeys, this.props.resetKeys)) {
      this.setState({ error: null });
    }
  }

  reset = (): void => {
    this.props.onReset?.();
    this.setState({ error: null });
  };

  render(): ReactNode {
    const { error } = this.state;
    const { label, fallback, children } = this.props;

    if (error === null) return children;

    if (typeof fallback === "function") return fallback(error, this.reset);
    if (fallback !== undefined) return fallback;

    return (
      <div className="arbor-banner is-error" role="alert" data-testid={`error-boundary-${label}`}>
        <span>This panel hit an error.</span>
        <button type="button" data-testid={`error-boundary-${label}-reset`} onClick={this.reset}>
          Try again
        </button>
      </div>
    );
  }
}

// Shallow, length-aware compare of two resetKeys arrays (either may be undefined).
function keysChanged(prev: unknown[] | undefined, next: unknown[] | undefined): boolean {
  if (prev === next) return false;
  if (prev === undefined || next === undefined) return true;
  if (prev.length !== next.length) return true;
  return prev.some((k, i) => !Object.is(k, next[i]));
}
