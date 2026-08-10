import { render, screen, fireEvent } from "@testing-library/react";
import AppError from "./error";

const makeError = () => Object.assign(new Error("boom"), { digest: "abc123" });

test("names the problem without dumping a stack trace at the user", () => {
  render(<AppError error={makeError()} reset={jest.fn()} />);
  expect(screen.getByRole("heading", { name: /something went wrong/i })).toBeInTheDocument();
  expect(screen.queryByText(/boom/)).not.toBeInTheDocument();
});

test("offers a retry that calls reset rather than reloading the whole app", () => {
  const reset = jest.fn();
  render(<AppError error={makeError()} reset={reset} />);
  fireEvent.click(screen.getByRole("button", { name: /try again/i }));
  expect(reset).toHaveBeenCalledTimes(1);
});

test("offers a way back to the dashboard when retrying will not help", () => {
  render(<AppError error={makeError()} reset={jest.fn()} />);
  expect(screen.getByRole("link", { name: /dashboard/i })).toHaveAttribute("href", "/dashboard");
});

test("surfaces the digest so a user can quote it to support", () => {
  render(<AppError error={makeError()} reset={jest.fn()} />);
  expect(screen.getByText(/abc123/)).toBeInTheDocument();
});
