import { render, screen, fireEvent } from "@testing-library/react";
import { Button } from "./Button";

test("is a real button that reports what it does", () => {
  const onClick = jest.fn();
  render(<Button onClick={onClick}>Save changes</Button>);

  const button = screen.getByRole("button", { name: "Save changes" });
  fireEvent.click(button);

  expect(button).toHaveAttribute("type", "button");
  expect(onClick).toHaveBeenCalledTimes(1);
});

test("does not submit a form unless it is asked to", () => {
  // A bare <button> inside a form defaults to type=submit, which has posted
  // forms that only meant to open a picker.
  render(
    <form>
      <Button>Add a row</Button>
      <Button type="submit">Save</Button>
    </form>,
  );

  expect(screen.getByRole("button", { name: "Add a row" })).toHaveAttribute("type", "button");
  expect(screen.getByRole("button", { name: "Save" })).toHaveAttribute("type", "submit");
});

test("a disabled button neither fires nor pretends it can", () => {
  const onClick = jest.fn();
  render(
    <Button disabled onClick={onClick}>
      Launch
    </Button>,
  );

  const button = screen.getByRole("button", { name: "Launch" });
  fireEvent.click(button);

  expect(button).toBeDisabled();
  expect(onClick).not.toHaveBeenCalled();
});

test("while busy it says so, stays named, and cannot be fired twice", () => {
  const onClick = jest.fn();
  render(
    <Button busy busyLabel="Saving..." onClick={onClick}>
      Save
    </Button>,
  );

  const button = screen.getByRole("button", { name: "Saving..." });
  fireEvent.click(button);

  expect(onClick).not.toHaveBeenCalled();
  expect(button).toHaveAttribute("aria-busy", "true");
});

test("carries its own variant, and a caller can still add classes", () => {
  const { rerender } = render(<Button variant="danger">Delete</Button>);
  expect(screen.getByRole("button", { name: "Delete" }).className).toContain("bg-red-600");

  rerender(
    <Button variant="secondary" className="w-full">
      Cancel
    </Button>,
  );
  const secondary = screen.getByRole("button", { name: "Cancel" });
  expect(secondary.className).toContain("w-full");
  expect(secondary.className).not.toContain("bg-red-600");
});

test("every variant keeps a visible focus ring", () => {
  for (const variant of ["primary", "secondary", "danger", "ghost"] as const) {
    const { unmount } = render(<Button variant={variant}>Go</Button>);
    expect(screen.getByRole("button", { name: "Go" }).className).toContain("focus-visible:");
    unmount();
  }
});

test("forwards the attributes a caller needs to name or describe it", () => {
  render(<Button aria-label="Close the panel" data-testid="closer" title="Close" />);

  const button = screen.getByRole("button", { name: "Close the panel" });
  expect(button).toHaveAttribute("data-testid", "closer");
  expect(button).toHaveAttribute("title", "Close");
});
