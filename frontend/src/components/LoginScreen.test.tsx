// Runnable: bench-free (vitest + jsdom; no Frappe, no running app).
//
// LoginScreen — a provider-agnostic username/password form that POSTs to the
// Frappe-native /api/method/login (usr/pwd) and, on success, calls
// onAuthenticated (the shell re-checks whoami). On bad creds it shows an inline
// error and does NOT call onAuthenticated. OSS-clean: zero auth-vendor strings.

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LoginScreen } from "./LoginScreen";

function fillAndSubmit(usr: string, pwd: string): void {
  fireEvent.change(screen.getByTestId("login-username"), { target: { value: usr } });
  fireEvent.change(screen.getByTestId("login-password"), { target: { value: pwd } });
  fireEvent.click(screen.getByTestId("login-submit"));
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("LoginScreen", () => {
  it("renders a username/password form and a submit button", () => {
    render(<LoginScreen onAuthenticated={vi.fn()} fetchImpl={vi.fn()} />);
    expect(screen.getByTestId("login-screen")).toBeInTheDocument();
    expect(screen.getByTestId("login-username")).toBeInTheDocument();
    expect(screen.getByTestId("login-password")).toBeInTheDocument();
    expect(screen.getByTestId("login-submit")).toBeInTheDocument();
  });

  it("POSTs usr/pwd to /api/method/login and calls onAuthenticated on success", async () => {
    const onAuthenticated = vi.fn();
    const fetchImpl = vi.fn().mockResolvedValue({ ok: true } as Response);
    render(<LoginScreen onAuthenticated={onAuthenticated} fetchImpl={fetchImpl} />);

    fillAndSubmit("alice", "s3cret");

    await waitFor(() => expect(onAuthenticated).toHaveBeenCalledTimes(1));
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe("/api/method/login");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ usr: "alice", pwd: "s3cret" });
    expect(screen.queryByTestId("login-error")).toBeNull();
  });

  it("shows an inline error on bad creds and does NOT call onAuthenticated", async () => {
    const onAuthenticated = vi.fn();
    const fetchImpl = vi.fn().mockResolvedValue({ ok: false, status: 401 } as Response);
    render(<LoginScreen onAuthenticated={onAuthenticated} fetchImpl={fetchImpl} />);

    fillAndSubmit("alice", "wrong");

    expect(await screen.findByTestId("login-error")).toBeInTheDocument();
    expect(onAuthenticated).not.toHaveBeenCalled();
  });

  it("shows an inline error when the network request rejects", async () => {
    const onAuthenticated = vi.fn();
    const fetchImpl = vi.fn().mockRejectedValue(new Error("network"));
    render(<LoginScreen onAuthenticated={onAuthenticated} fetchImpl={fetchImpl} />);

    fillAndSubmit("alice", "s3cret");

    expect(await screen.findByTestId("login-error")).toBeInTheDocument();
    expect(onAuthenticated).not.toHaveBeenCalled();
  });

  it("does not submit when username or password is empty", () => {
    const onAuthenticated = vi.fn();
    const fetchImpl = vi.fn();
    render(<LoginScreen onAuthenticated={onAuthenticated} fetchImpl={fetchImpl} />);

    fireEvent.click(screen.getByTestId("login-submit"));
    expect(fetchImpl).not.toHaveBeenCalled();

    fireEvent.change(screen.getByTestId("login-username"), { target: { value: "alice" } });
    fireEvent.click(screen.getByTestId("login-submit"));
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("disables the submit button while the request is in flight", async () => {
    let resolve!: (v: Response) => void;
    const fetchImpl = vi.fn().mockReturnValue(new Promise<Response>((r) => (resolve = r)));
    render(<LoginScreen onAuthenticated={vi.fn()} fetchImpl={fetchImpl} />);

    fillAndSubmit("alice", "s3cret");
    await waitFor(() => expect(screen.getByTestId("login-submit")).toBeDisabled());

    resolve({ ok: true } as Response);
    await waitFor(() => expect(screen.getByTestId("login-submit")).not.toBeDisabled());
  });

  it("clears a prior error when the form is resubmitted", async () => {
    const onAuthenticated = vi.fn();
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce({ ok: false, status: 401 } as Response)
      .mockResolvedValueOnce({ ok: true } as Response);
    render(<LoginScreen onAuthenticated={onAuthenticated} fetchImpl={fetchImpl} />);

    fillAndSubmit("alice", "wrong");
    expect(await screen.findByTestId("login-error")).toBeInTheDocument();

    fireEvent.change(screen.getByTestId("login-password"), { target: { value: "right" } });
    fireEvent.click(screen.getByTestId("login-submit"));
    await waitFor(() => expect(onAuthenticated).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId("login-error")).toBeNull();
  });
});
