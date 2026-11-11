import { render, screen } from "@testing-library/react";
import { Button, buttonClass } from "./Button";

test("a link styled as an action uses the same classes as the button", () => {
  render(
    <>
      <Button variant="primary">Go</Button>
      <a href="/x" className={buttonClass()}>
        Link
      </a>
      <Button variant="secondary" size="sm">
        Small
      </Button>
      <a href="/y" className={buttonClass({ variant: "secondary", size: "sm" })}>
        Small link
      </a>
    </>,
  );
  expect(screen.getByRole("link", { name: "Link" }).className).toBe(screen.getByRole("button", { name: "Go" }).className);
  expect(screen.getByRole("link", { name: "Small link" }).className).toBe(
    screen.getByRole("button", { name: "Small" }).className,
  );
});

test("the large size keeps the launch dialog's footer hit target", () => {
  render(<Button size="lg">Launch</Button>);
  expect(screen.getByRole("button", { name: "Launch" }).className).toMatch(/\bpx-6\b.*\bpy-2\.5\b/);
});
