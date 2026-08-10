import { render, screen } from "@testing-library/react";
import { LoadingSpinner } from "./LoadingSpinner";

// This component is rendered at 63 sites across the app, which is every loading
// state that does not hand-roll its own `animate-spin`. Before this test it was
// a pair of unlabelled <div>s: a screen reader reached a loading page and was
// told nothing at all, so "the app is working" was conveyed purely by a visual
// animation.
//
// Note these assert text CONTENT, not accessible name. `status` is not a
// name-from-content role, so an accessible name would have to come from
// aria-label, and an aria-label on an empty live region announces nothing. The
// canonical spinner pattern is a `role="status"` wrapper holding visually
// hidden text, and it is the text that gets announced.

test("a spinner announces that something is loading", () => {
  render(<LoadingSpinner />);

  // role="status" is the polite live region: it reports app state without
  // interrupting whatever the user is reading, which is right for "loading".
  expect(screen.getByRole("status")).toHaveTextContent("Loading");
});

test("the loading text is not visible, so the layout is unchanged at 63 sites", () => {
  render(<LoadingSpinner />);

  // `sr-only` clips it to a 1px box rather than hiding it, which keeps it in
  // the accessibility tree. `hidden`/`display:none` would remove it from both.
  expect(screen.getByText("Loading")).toHaveClass("sr-only");
});

test("the announcement can name what is loading, for pages with several", () => {
  render(<LoadingSpinner label="Loading samples" />);

  expect(screen.getByRole("status")).toHaveTextContent("Loading samples");
});

test("the spinning element is hidden from assistive tech, so nothing is doubled", () => {
  const { container } = render(<LoadingSpinner />);

  // The animated ring is decorative. Left exposed it is a second child of the
  // live region and screen readers reach an unlabelled node beside the text.
  expect(container.querySelector(".animate-spin")).toHaveAttribute(
    "aria-hidden",
    "true",
  );
});

test("the animation is suppressed when the user asks for reduced motion", () => {
  const { container } = render(<LoadingSpinner />);

  // jsdom does not evaluate media queries against Tailwind's compiled CSS, so
  // assert the variant that carries the rule. `motion-reduce:animate-none`
  // compiles to `@media (prefers-reduced-motion: reduce) { animation: none }`.
  // A spinner is the app's most common animation and it never stops, which is
  // exactly the vestibular trigger the media query exists for.
  expect(container.querySelector(".animate-spin")).toHaveClass(
    "motion-reduce:animate-none",
  );
});

test("size stays a presentational choice and does not change the semantics", () => {
  render(<LoadingSpinner size="lg" />);

  expect(screen.getByRole("status")).toHaveTextContent("Loading");
});
