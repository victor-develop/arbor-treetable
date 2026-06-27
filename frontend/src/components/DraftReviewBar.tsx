// Draft flow — the sticky "Review N change(s)" bar. Shown ONLY while a non-owner
// has >=1 unsubmitted cell draft (owners commit directly and never see it).
// Clicking it opens the DraftReviewModal. It owns zero mutation logic: a thin
// signifier that the actor has staged-but-unsent changes, plus the entry point
// to review/submit them. The shell gates its mount on draftCount > 0, but the
// component self-guards too (returns null at 0) so it can never render empty.

export function DraftReviewBar({
  count,
  onReview,
}: {
  // How many cell drafts are staged (draftCount from useSheet).
  count: number;
  // Open the DraftReviewModal.
  onReview: () => void;
}): JSX.Element | null {
  if (count <= 0) return null;
  const noun = count === 1 ? "change" : "changes";
  return (
    <div className="arbor-draft-bar" data-testid="draft-bar" role="status">
      <span className="arbor-draft-bar-label" data-testid="draft-bar-count">
        {count} unsent {noun}
      </span>
      <button
        type="button"
        className="arbor-draft-bar-review"
        data-testid="draft-bar-review"
        onClick={onReview}
      >
        Review {count} {noun}
      </button>
    </div>
  );
}
