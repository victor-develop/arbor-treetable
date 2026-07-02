// Per-cell comments — a right-edge drawer showing ONE cell's comment thread(s).
// Pure presentational + callbacks: it renders the server-fetched thread (author,
// time, body, threaded replies, @mention chips) and a composer, and funnels
// post/resolve/reopen/delete through host callbacks — it owns zero fetch and
// re-derives no ACL (the can_resolve / can_delete server hints gate the
// affordances; the shim re-enforces authority).
//
// Stacking: the drawer is a fixed right panel pinned BELOW the agent popup
// (.arbor-agent-dock, z 60) but above the table — see .arbor-comment-drawer in
// styles.css. In Proposed preview (`readOnly`) it is INERT: the thread stays
// readable but the composer + every action affordance are withheld (mirroring
// how a Cell renders static in preview).

import { useEffect, useMemo, useState } from "react";
import type { CellComment } from "../api";
import { TrashIcon } from "./icons";

// The (node, column) the drawer is opened for, plus the readable column label
// (resolved by the host from the snapshot) for the header.
export type CommentCell = { node: string; column: string; label: string };

export function CommentDrawer({
  open,
  cell,
  comments,
  readOnly,
  canComment = true,
  onPost,
  onResolve,
  onReopen,
  onDelete,
  onClose,
}: {
  open: boolean;
  // The targeted cell; null renders nothing (drawer closed).
  cell: CommentCell | null;
  // The cell's thread list (creation-asc), as returned by listCellComments. The
  // drawer groups it by thread_root client-side.
  comments: CellComment[];
  // Proposed-preview inert mode: thread is view-only (no composer, no actions).
  readOnly?: boolean;
  // Whether the viewer may post at all (read-ACL); default true. When false the
  // composer is withheld even outside preview.
  canComment?: boolean;
  // Post a comment. `parent` is the thread root's name when replying (else
  // undefined for a new root thread on the cell).
  onPost: (body: string, parent?: string) => void;
  // Resolve / reopen a THREAD (by its root comment name). Shown only when the
  // root carries can_resolve.
  onResolve?: (comment: string) => void;
  onReopen?: (comment: string) => void;
  // Delete a comment (by name). Shown only when it carries can_delete.
  onDelete?: (comment: string) => void;
  onClose: () => void;
}): JSX.Element | null {
  // The composer's target: null = a new root thread; a root name = a reply.
  const [replyTo, setReplyTo] = useState<string | null>(null);
  const [body, setBody] = useState("");

  // Reset the composer whenever the targeted cell changes (or the drawer closes)
  // so a stale draft never leaks between cells.
  useEffect(() => {
    setReplyTo(null);
    setBody("");
  }, [cell?.node, cell?.column, open]);

  // Group the flat thread list into root → replies. A root is a comment whose
  // thread_root is null; replies carry thread_root = the root's name. Ordering
  // follows the (creation-asc) input.
  const threads = useMemo(() => groupThreads(comments), [comments]);

  if (!open || !cell) return null;

  const canWrite = !readOnly && canComment;
  const trimmed = body.trim();

  const submit = () => {
    if (!trimmed) return;
    onPost(trimmed, replyTo ?? undefined);
    setBody("");
    setReplyTo(null);
  };

  return (
    <aside
      className="arbor-comment-drawer"
      data-testid="comment-drawer"
      role="dialog"
      aria-label={`Comments on ${cell.label}`}
      data-readonly={readOnly ? "true" : undefined}
      // Escape closes (parity with the modal shell).
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation();
          onClose();
        }
      }}
    >
      <header className="arbor-comment-drawer-head">
        <span className="arbor-comment-drawer-title">
          Comments · <strong>{cell.label}</strong>
        </span>
        <button
          type="button"
          className="arbor-comment-drawer-close"
          data-testid="comment-drawer-close"
          aria-label="Close comments"
          onClick={onClose}
        >
          ✕
        </button>
      </header>

      <div className="arbor-comment-drawer-body">
        {threads.length === 0 ? (
          <p className="arbor-comment-empty" data-testid="comment-drawer-empty">
            No comments on this cell yet.
          </p>
        ) : (
          threads.map((t) => (
            <section
              key={t.root.name}
              className="arbor-comment-thread"
              data-testid={`comment-thread-${t.root.name}`}
              data-resolved={t.root.resolved ? "true" : undefined}
            >
              <CommentCard
                comment={t.root}
                isRoot
                readOnly={!!readOnly}
                onResolve={onResolve}
                onReopen={onReopen}
                onDelete={onDelete}
                onReply={canWrite ? () => setReplyTo(t.root.name) : undefined}
              />
              {t.replies.map((r) => (
                <CommentCard
                  key={r.name}
                  comment={r}
                  readOnly={!!readOnly}
                  onDelete={onDelete}
                />
              ))}
            </section>
          ))
        )}
      </div>

      {canWrite && (
        <footer className="arbor-comment-composer-foot">
          {replyTo && (
            <div className="arbor-comment-replying" data-testid="comment-replying">
              <span>Replying to thread</span>
              <button
                type="button"
                className="arbor-comment-reply-cancel"
                data-testid="comment-reply-cancel"
                onClick={() => setReplyTo(null)}
              >
                Cancel
              </button>
            </div>
          )}
          <textarea
            className="arbor-comment-composer"
            data-testid="comment-composer"
            placeholder="Add a comment… use @name to mention"
            value={body}
            onChange={(e) => setBody(e.target.value)}
            onKeyDown={(e) => {
              // Enter posts (Shift+Enter is a newline), mirroring the cell editor.
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
          />
          <div className="arbor-comment-composer-actions">
            <button
              type="button"
              className="arbor-comment-post"
              data-testid="comment-post"
              disabled={!trimmed}
              onClick={submit}
            >
              {replyTo ? "Reply" : "Comment"}
            </button>
          </div>
        </footer>
      )}
    </aside>
  );
}

// One comment card — author, relative-ish timestamp, body, @mention chips, and
// (for a root, when permitted) resolve/reopen + reply; delete when can_delete.
function CommentCard({
  comment,
  isRoot,
  readOnly,
  onResolve,
  onReopen,
  onDelete,
  onReply,
}: {
  comment: CellComment;
  isRoot?: boolean;
  readOnly: boolean;
  onResolve?: (comment: string) => void;
  onReopen?: (comment: string) => void;
  onDelete?: (comment: string) => void;
  onReply?: () => void;
}): JSX.Element {
  const { name, author, body, mentions, resolved, timestamp } = comment;
  return (
    <article
      className="arbor-comment"
      data-testid={`comment-${name}`}
      data-reply={isRoot ? undefined : "true"}
    >
      <div className="arbor-comment-meta">
        <span className="arbor-comment-author">{author}</span>
        <time className="arbor-comment-time" dateTime={timestamp} title={timestamp}>
          {formatTimestamp(timestamp)}
        </time>
        {isRoot && resolved && (
          <span
            className="arbor-comment-resolved"
            data-testid={`comment-resolved-${name}`}
            title="Thread resolved"
          >
            resolved
          </span>
        )}
      </div>
      <p className="arbor-comment-body">{body}</p>
      {mentions.length > 0 && (
        <div className="arbor-comment-mentions">
          {mentions.map((m) => (
            <span
              key={m}
              className="arbor-comment-mention"
              data-testid={`comment-mention-${m}`}
            >
              @{m}
            </span>
          ))}
        </div>
      )}
      {!readOnly && (
        <div className="arbor-comment-actions">
          {isRoot && onReply && (
            <button
              type="button"
              className="arbor-comment-reply"
              data-testid={`comment-reply-${name}`}
              onClick={onReply}
            >
              Reply
            </button>
          )}
          {isRoot &&
            comment.can_resolve &&
            (resolved
              ? onReopen && (
                  <button
                    type="button"
                    className="arbor-comment-reopen"
                    data-testid={`comment-reopen-${name}`}
                    onClick={() => onReopen(name)}
                  >
                    Reopen
                  </button>
                )
              : onResolve && (
                  <button
                    type="button"
                    className="arbor-comment-resolve"
                    data-testid={`comment-resolve-${name}`}
                    onClick={() => onResolve(name)}
                  >
                    Resolve
                  </button>
                ))}
          {comment.can_delete && onDelete && (
            <button
              type="button"
              className="arbor-comment-delete"
              data-testid={`comment-delete-${name}`}
              aria-label="Delete comment"
              title="Delete comment"
              onClick={() => onDelete(name)}
            >
              <TrashIcon size={13} />
            </button>
          )}
        </div>
      )}
    </article>
  );
}

type Thread = { root: CellComment; replies: CellComment[] };

// Group a flat (creation-asc) comment list into root threads + their replies. A
// reply whose root isn't present (e.g. a redacted root) still surfaces as its
// own thread so it never silently vanishes.
export function groupThreads(comments: CellComment[]): Thread[] {
  const roots = new Map<string, Thread>();
  const order: string[] = [];
  const ensure = (c: CellComment): Thread => {
    let t = roots.get(c.name);
    if (!t) {
      t = { root: c, replies: [] };
      roots.set(c.name, t);
      order.push(c.name);
    } else if (t.root === PLACEHOLDER) {
      // A reply created the placeholder first; now fill in the real root.
      t.root = c;
    }
    return t;
  };
  for (const c of comments) {
    if (c.thread_root == null) {
      ensure(c);
    } else {
      let t = roots.get(c.thread_root);
      if (!t) {
        t = { root: PLACEHOLDER, replies: [] };
        roots.set(c.thread_root, t);
        order.push(c.thread_root);
      }
      t.replies.push(c);
    }
  }
  // Drop any thread that never got a real root (defensive; shouldn't happen).
  return order.map((k) => roots.get(k)!).filter((t) => t.root !== PLACEHOLDER);
}

// A sentinel root standing in for a not-yet-seen thread_root while grouping.
const PLACEHOLDER = {
  name: "",
  thread_root: null,
  parent_comment: null,
  author: "",
  body: "",
  mentions: [],
  resolved: false,
  resolved_by: null,
  resolved_at: null,
  timestamp: "",
  can_resolve: false,
  can_delete: false,
} as CellComment;

// Compact local "Jun 20, 10:00" timestamp (mirrors ActivityPanel.formatTimestamp);
// the raw ISO stays in the <time datetime> for machine/tooltip use.
function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
