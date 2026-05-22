import { render, screen, fireEvent } from "@testing-library/react";

jest.mock("next/link", () => {
  return function MockLink({
    href,
    children,
    ...rest
  }: {
    href: string;
    children: React.ReactNode;
  }) {
    return (
      <a href={typeof href === "string" ? href : "#"} {...rest}>
        {children}
      </a>
    );
  };
});

import { QuickCreateMenu } from "./QuickCreateMenu";

test("is collapsed until the + New button is clicked", () => {
  render(<QuickCreateMenu />);
  expect(screen.queryByText("New Experiment")).not.toBeInTheDocument();
});

test("opens to show the create actions with their links", () => {
  render(<QuickCreateMenu />);
  fireEvent.click(screen.getByRole("button", { name: /new/i }));

  expect(screen.getByRole("link", { name: "New Project" })).toHaveAttribute(
    "href",
    "/projects?new=1",
  );
  expect(screen.getByRole("link", { name: "New Experiment" })).toHaveAttribute(
    "href",
    "/experiments/new",
  );
  expect(screen.getByRole("link", { name: "New Sample" })).toHaveAttribute(
    "href",
    "/experiments/new",
  );
});
