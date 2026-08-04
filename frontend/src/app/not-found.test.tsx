import { render, screen } from "@testing-library/react";
import NotFound from "./not-found";

test("names the problem in plain language", () => {
  render(<NotFound />);
  expect(screen.getByRole("heading", { name: /page not found/i })).toBeInTheDocument();
});

test("always offers a way back into the app", () => {
  render(<NotFound />);
  const home = screen.getByRole("link", { name: /dashboard/i });
  expect(home).toHaveAttribute("href", "/dashboard");
});

test("does not dead-end: a second escape route is offered", () => {
  render(<NotFound />);
  // A 404 with a single link is a dead end if that link is also wrong for the user.
  expect(screen.getAllByRole("link").length).toBeGreaterThanOrEqual(2);
});
