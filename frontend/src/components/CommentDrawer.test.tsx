import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { CommentDrawer } from "./CommentDrawer";
import type { CellComment } from "../api";

// Build a comment with sensible defaults; override per-test. `can_resolve` /
// `can_delete` are DISPLAY-only server hints — the drawer renders affordances off
// them and never re-derives ACL.
const cmt = (over: Partial<CellComment>): CellComment => ({
  name: "c1",
  thread_root: null,
  parent_comment: null,
  author: "alice@x.io",
  body: "First!",
  mentions: [],
  resolved: false,
  resolved_by: null,
  resolved_at: null,
  timestamp: "2026-06-20T10:00:00Z",
  can_resolve: false,
  can_delete: false,
  ...over,
});

const cell = { node: "n1", column: "col:budget", label: "Budget" };

describe("CommentDrawer — open/close chrome", () => {
  it("renders nothing when closed", () => {
    const { container } = render(
      <CommentDrawer open={false} cell={cell} comments={[]} onClose={() => {}} onPost={() => {}} />,
    );
    expect(container.querySelector('[data-testid="comment-drawer"]')).toBeNull();
  });

  it("renders the drawer + the cell label when open", () => {
    render(
      <CommentDrawer open cell={cell} comments={[]} onClose={() => {}} onPost={() => {}} />,
    );
    const drawer = screen.getByTestId("comment-drawer");
    expect(drawer).toBeInTheDocument();
    expect(drawer).toHaveAttribute("role", "dialog");
    expect(drawer).toHaveTextContent("Budget");
  });

  it("the ✕ button calls onClose", () => {
    const onClose = vi.fn();
    render(<CommentDrawer open cell={cell} comments={[]} onClose={onClose} onPost={() => {}} />);
    fireEvent.click(screen.getByTestId("comment-drawer-close"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("Escape closes the drawer", () => {
    const onClose = vi.fn();
    render(<CommentDrawer open cell={cell} comments={[]} onClose={onClose} onPost={() => {}} />);
    fireEvent.keyDown(screen.getByTestId("comment-drawer"), { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("shows an empty-state when the cell has no comments", () => {
    render(<CommentDrawer open cell={cell} comments={[]} onClose={() => {}} onPost={() => {}} />);
    expect(screen.getByTestId("comment-drawer-empty")).toBeInTheDocument();
  });
});

describe("CommentDrawer — thread rendering", () => {
  const thread: CellComment[] = [
    cmt({ name: "root", body: "Is this final?", author: "alice@x.io" }),
    cmt({
      name: "reply1",
      thread_root: "root",
      parent_comment: "root",
      body: "Not yet.",
      author: "bob@x.io",
    }),
  ];

  it("renders author + body for each comment, grouped by thread", () => {
    render(<CommentDrawer open cell={cell} comments={thread} onClose={() => {}} onPost={() => {}} />);
    expect(screen.getByTestId("comment-root")).toHaveTextContent("Is this final?");
    expect(screen.getByTestId("comment-root")).toHaveTextContent("alice@x.io");
    const reply = screen.getByTestId("comment-reply1");
    expect(reply).toHaveTextContent("Not yet.");
    expect(reply).toHaveTextContent("bob@x.io");
    // The reply is nested under its thread root (data-reply marks nesting).
    expect(reply).toHaveAttribute("data-reply", "true");
  });

  it("renders @mention chips on a comment", () => {
    render(
      <CommentDrawer
        open
        cell={cell}
        comments={[cmt({ name: "root", mentions: ["carol@x.io"] })]}
        onClose={() => {}}
        onPost={() => {}}
      />,
    );
    expect(screen.getByTestId("comment-mention-carol@x.io")).toHaveTextContent("carol@x.io");
  });

  it("renders a resolved thread with a resolved marker", () => {
    render(
      <CommentDrawer
        open
        cell={cell}
        comments={[cmt({ name: "root", resolved: true, resolved_by: "alice@x.io" })]}
        onClose={() => {}}
        onPost={() => {}}
      />,
    );
    expect(screen.getByTestId("comment-resolved-root")).toBeInTheDocument();
  });
});

describe("CommentDrawer — composer (post)", () => {
  it("posting a non-empty body calls onPost with the trimmed text and clears the field", () => {
    const onPost = vi.fn();
    render(<CommentDrawer open cell={cell} comments={[]} onClose={() => {}} onPost={onPost} />);
    const box = screen.getByTestId("comment-composer") as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "  looks good  " } });
    fireEvent.click(screen.getByTestId("comment-post"));
    expect(onPost).toHaveBeenCalledTimes(1);
    expect(onPost).toHaveBeenCalledWith("looks good", undefined);
    expect(box.value).toBe("");
  });

  it("an empty / whitespace-only body does NOT post (button disabled)", () => {
    const onPost = vi.fn();
    render(<CommentDrawer open cell={cell} comments={[]} onClose={() => {}} onPost={onPost} />);
    fireEvent.change(screen.getByTestId("comment-composer"), { target: { value: "   " } });
    expect(screen.getByTestId("comment-post")).toBeDisabled();
    fireEvent.click(screen.getByTestId("comment-post"));
    expect(onPost).not.toHaveBeenCalled();
  });

  it("Replying to a thread threads the parent_comment through onPost", () => {
    const onPost = vi.fn();
    render(
      <CommentDrawer
        open
        cell={cell}
        comments={[cmt({ name: "root", body: "root" })]}
        onClose={() => {}}
        onPost={onPost}
      />,
    );
    // Open the reply composer for the root thread.
    fireEvent.click(screen.getByTestId("comment-reply-root"));
    const box = screen.getByTestId("comment-composer");
    fireEvent.change(box, { target: { value: "a reply" } });
    fireEvent.click(screen.getByTestId("comment-post"));
    expect(onPost).toHaveBeenCalledWith("a reply", "root");
  });
});

describe("CommentDrawer — resolve/reopen gating (can_resolve)", () => {
  it("shows Resolve only when can_resolve is true; clicking calls onResolve", () => {
    const onResolve = vi.fn();
    render(
      <CommentDrawer
        open
        cell={cell}
        comments={[cmt({ name: "root", can_resolve: true })]}
        onClose={() => {}}
        onPost={() => {}}
        onResolve={onResolve}
      />,
    );
    fireEvent.click(screen.getByTestId("comment-resolve-root"));
    expect(onResolve).toHaveBeenCalledWith("root");
  });

  it("hides Resolve when can_resolve is false", () => {
    render(
      <CommentDrawer
        open
        cell={cell}
        comments={[cmt({ name: "root", can_resolve: false })]}
        onClose={() => {}}
        onPost={() => {}}
        onResolve={() => {}}
      />,
    );
    expect(screen.queryByTestId("comment-resolve-root")).toBeNull();
  });

  it("a resolved thread shows Reopen (can_resolve) wired to onReopen", () => {
    const onReopen = vi.fn();
    render(
      <CommentDrawer
        open
        cell={cell}
        comments={[cmt({ name: "root", resolved: true, can_resolve: true })]}
        onClose={() => {}}
        onPost={() => {}}
        onReopen={onReopen}
      />,
    );
    fireEvent.click(screen.getByTestId("comment-reopen-root"));
    expect(onReopen).toHaveBeenCalledWith("root");
  });
});

describe("CommentDrawer — delete gating (can_delete)", () => {
  it("shows Delete only when can_delete; clicking calls onDelete", () => {
    const onDelete = vi.fn();
    render(
      <CommentDrawer
        open
        cell={cell}
        comments={[cmt({ name: "root", can_delete: true })]}
        onClose={() => {}}
        onPost={() => {}}
        onDelete={onDelete}
      />,
    );
    fireEvent.click(screen.getByTestId("comment-delete-root"));
    expect(onDelete).toHaveBeenCalledWith("root");
  });

  it("hides Delete when can_delete is false", () => {
    render(
      <CommentDrawer
        open
        cell={cell}
        comments={[cmt({ name: "root", can_delete: false })]}
        onClose={() => {}}
        onPost={() => {}}
        onDelete={() => {}}
      />,
    );
    expect(screen.queryByTestId("comment-delete-root")).toBeNull();
  });
});

describe("CommentDrawer — inert in Proposed preview", () => {
  it("readOnly hides the composer + every action (thread is view-only)", () => {
    const onPost = vi.fn();
    render(
      <CommentDrawer
        open
        readOnly
        cell={cell}
        comments={[cmt({ name: "root", can_resolve: true, can_delete: true })]}
        onClose={() => {}}
        onPost={onPost}
        onResolve={() => {}}
        onDelete={() => {}}
      />,
    );
    // Body still readable...
    expect(screen.getByTestId("comment-root")).toBeInTheDocument();
    // ...but no composer, no post, no resolve/delete/reply affordances.
    expect(screen.queryByTestId("comment-composer")).toBeNull();
    expect(screen.queryByTestId("comment-post")).toBeNull();
    expect(screen.queryByTestId("comment-resolve-root")).toBeNull();
    expect(screen.queryByTestId("comment-delete-root")).toBeNull();
    expect(screen.queryByTestId("comment-reply-root")).toBeNull();
  });
});

describe("CommentDrawer — stacking below the agent dock", () => {
  it("carries a class the CSS pins below .arbor-agent-dock (z-index contract)", () => {
    render(<CommentDrawer open cell={cell} comments={[]} onClose={() => {}} onPost={() => {}} />);
    // The stable hook the stylesheet targets; the actual z-order is asserted in CSS.
    expect(screen.getByTestId("comment-drawer")).toHaveClass("arbor-comment-drawer");
  });
});
