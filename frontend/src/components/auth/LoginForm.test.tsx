import { render, screen, fireEvent } from "@testing-library/react";

jest.mock("next/link", () => {
  return function MockLink({ children, href }: { children: React.ReactNode; href: string }) {
    return <a href={href}>{children}</a>;
  };
});

import { LoginForm } from "./LoginForm";

test("renders a Forgot password link to the forgot-password page", () => {
  render(<LoginForm onSubmit={jest.fn()} />);
  const link = screen.getByRole("link", { name: /forgot password/i });
  expect(link).toHaveAttribute("href", "/forgot-password");
});

test("submits the entered email and password", async () => {
  const onSubmit = jest.fn().mockResolvedValue(undefined);
  render(<LoginForm onSubmit={onSubmit} />);
  fireEvent.change(screen.getByLabelText("Email"), { target: { value: "a@b.org" } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "secret123" } });
  fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
  expect(onSubmit).toHaveBeenCalledWith("a@b.org", "secret123");
});
