import { renderHook, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useRealtime } from "./useRealtime";

// A stand-in for the browser's EventSource: records the URL it was opened with,
// lets a test fire server events, and tracks close() so teardown is assertable.
class FakeEventSource {
  static instances: FakeEventSource[] = [];
  url: string;
  withCredentials: boolean;
  closed = false;
  private listeners: Record<string, Array<() => void>> = {};

  constructor(url: string, init?: { withCredentials?: boolean }) {
    this.url = url;
    this.withCredentials = Boolean(init?.withCredentials);
    FakeEventSource.instances.push(this);
  }
  addEventListener(kind: string, fn: () => void): void {
    (this.listeners[kind] ??= []).push(fn);
  }
  close(): void {
    this.closed = true;
  }
  emit(kind: string): void {
    (this.listeners[kind] ?? []).forEach((fn) => fn());
  }
}

describe("useRealtime", () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    vi.stubGlobal("EventSource", FakeEventSource);
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  const latest = () => FakeEventSource.instances.at(-1)!;

  it("subscribes to the sheet's stream with credentials", () => {
    renderHook(() => useRealtime("my sheet", vi.fn()));
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(latest().url).toBe("/api/method/arbor.events?sheet=my%20sheet");
    expect(latest().withCredentials).toBe(true);
  });

  it("opens nothing without a sheet", () => {
    renderHook(() => useRealtime(null, vi.fn()));
    expect(FakeEventSource.instances).toHaveLength(0);
  });

  it("is a silent no-op where EventSource does not exist", () => {
    vi.stubGlobal("EventSource", undefined);
    expect(() => renderHook(() => useRealtime("s1", vi.fn()))).not.toThrow();
  });

  it("delivers a comments signal to the callback", () => {
    const onSignal = vi.fn();
    renderHook(() => useRealtime("s1", onSignal));
    act(() => {
      latest().emit("comments");
      vi.advanceTimersByTime(500);
    });
    expect(onSignal).toHaveBeenCalledWith("comments");
  });

  it("coalesces a burst into ONE callback", () => {
    // Each marker means the same thing ("refetch"), so a thread of replies must
    // not turn into one refetch per reply.
    const onSignal = vi.fn();
    renderHook(() => useRealtime("s1", onSignal));
    act(() => {
      latest().emit("comments");
      latest().emit("comments");
      latest().emit("comments");
      vi.advanceTimersByTime(500);
    });
    expect(onSignal).toHaveBeenCalledTimes(1);
  });

  it("does not fire a signal that lands after unmount", () => {
    const onSignal = vi.fn();
    const { unmount } = renderHook(() => useRealtime("s1", onSignal));
    act(() => {
      latest().emit("comments");
    });
    unmount();
    act(() => {
      vi.advanceTimersByTime(500);
    });
    expect(onSignal).not.toHaveBeenCalled();
  });

  it("closes the stream on unmount", () => {
    const { unmount } = renderHook(() => useRealtime("s1", vi.fn()));
    const source = latest();
    unmount();
    expect(source.closed).toBe(true);
  });

  it("re-subscribes when the sheet changes, closing the old stream", () => {
    const { rerender } = renderHook(({ sheet }) => useRealtime(sheet, vi.fn()), {
      initialProps: { sheet: "s1" },
    });
    const first = latest();
    rerender({ sheet: "s2" });
    expect(first.closed).toBe(true);
    expect(FakeEventSource.instances).toHaveLength(2);
    expect(latest().url).toContain("sheet=s2");
  });

  it("keeps the stream open across a new callback identity", () => {
    // The callback is a fresh closure on every parent render; re-opening the
    // stream for each one would drop signals continuously.
    const { rerender } = renderHook<void, { cb: (kind: string) => void }>(
      ({ cb }) => useRealtime("s1", cb),
      { initialProps: { cb: vi.fn() } },
    );
    const second = vi.fn();
    rerender({ cb: second });
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(latest().closed).toBe(false);
    // The ref exists so the LATEST callback receives the signal; asserting only
    // that the stream survived would pass with the ref-sync effect deleted.
    act(() => {
      latest().emit("comments");
      vi.advanceTimersByTime(500);
    });
    expect(second).toHaveBeenCalledWith("comments");
  });

  it("keeps retrying through a short outage instead of giving up", () => {
    // A count-based budget died after ~6-9s of browser retries, which any
    // container restart exceeds — every open tab would lose realtime for good
    // after a routine deploy.
    renderHook(() => useRealtime("s1", vi.fn()));
    const source = latest();
    act(() => {
      source.emit("open");
      source.emit("error");
      vi.advanceTimersByTime(30_000);
      source.emit("error");
    });
    expect(source.closed).toBe(false);
  });

  it("gives up only after a prolonged outage", () => {
    renderHook(() => useRealtime("s1", vi.fn()));
    const source = latest();
    act(() => {
      source.emit("open");
      source.emit("error");
      vi.advanceTimersByTime(6 * 60 * 1000);
      source.emit("error");
    });
    expect(source.closed).toBe(true);
  });

  it("a recovery resets the outage clock", () => {
    const onSignal = vi.fn();
    renderHook(() => useRealtime("s1", onSignal));
    const source = latest();
    act(() => {
      source.emit("open");
      source.emit("error");
      vi.advanceTimersByTime(4 * 60 * 1000);
      source.emit("comments"); // recovered
      vi.advanceTimersByTime(500);
      source.emit("error");
      vi.advanceTimersByTime(4 * 60 * 1000);
      source.emit("error");
    });
    expect(source.closed).toBe(false);
    expect(onSignal).toHaveBeenCalledTimes(1);
  });

  it("re-arms on becoming visible again after giving up", () => {
    // A tab hidden through a long outage should recover when looked at, not
    // stay silently dead until a reload.
    renderHook(() => useRealtime("s1", vi.fn()));
    const first = latest();
    act(() => {
      first.emit("open");
      first.emit("error");
      vi.advanceTimersByTime(6 * 60 * 1000);
      first.emit("error");
    });
    expect(first.closed).toBe(true);
    expect(FakeEventSource.instances).toHaveLength(1);
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(FakeEventSource.instances).toHaveLength(2);
    expect(latest().closed).toBe(false);
  });

  it("does not re-open on visibility while the stream is healthy", () => {
    renderHook(() => useRealtime("s1", vi.fn()));
    act(() => {
      latest().emit("open");
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(FakeEventSource.instances).toHaveLength(1);
  });

});
