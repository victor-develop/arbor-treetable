// Draft flow — DraftReviewBar contract. Shown only while draftCount > 0; clicking
// "Review" opens the modal. Self-guards (renders nothing at 0).

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DraftReviewBar } from "./DraftReviewBar";

describe("DraftReviewBar", () => {
  it("renders the count + 'Review N changes' and fires onReview on click", () => {
    const onReview = vi.fn();
    render(<DraftReviewBar count={3} onReview={onReview} />);
    expect(screen.getByTestId("draft-bar")).toBeInTheDocument();
    expect(screen.getByTestId("draft-bar-review")).toHaveTextContent("Review 3 changes");
    fireEvent.click(screen.getByTestId("draft-bar-review"));
    expect(onReview).toHaveBeenCalledTimes(1);
  });

  it("uses the singular noun for a single change", () => {
    render(<DraftReviewBar count={1} onReview={vi.fn()} />);
    expect(screen.getByTestId("draft-bar-review")).toHaveTextContent("Review 1 change");
  });

  it("renders nothing when count is 0", () => {
    const { container } = render(<DraftReviewBar count={0} onReview={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });
});
