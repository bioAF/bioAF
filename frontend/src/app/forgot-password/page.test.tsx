import { render, screen, fireEvent, waitFor } from "@testing-library/react";

jest.mock("next/link", () => {
  return function MockLink({ children, href }: { children: React.ReactNode; href: string }) {
    return <a href={href}>{children}</a>;
  };
});

const mockPost = jest.fn();
jest.mock("@/lib/api", () => ({ api: { post: (...args: unknown[]) => mockPost(...args) } }));

import ForgotPasswordPage from "./page";

beforeEach(() => {
  mockPost.mockReset();
});

test("requests a reset and shows the generic confirmation", async () => {
  mockPost.mockResolvedValue({});
  render(<ForgotPasswordPage />);

  fireEvent.change(screen.getByLabelText("Email"), { target: { value: "user@lab.org" } });
  fireEvent.click(screen.getByRole("button", { name: /send reset link/i }));

  expect(mockPost).toHaveBeenCalledWith("/api/auth/request-reset", { email: "user@lab.org" });
  await waitFor(() => expect(screen.getByText(/check your email/i)).toBeInTheDocument());
});

test("still shows the confirmation when the request errors (no account enumeration)", async () => {
  mockPost.mockRejectedValue(new Error("boom"));
  render(<ForgotPasswordPage />);

  fireEvent.change(screen.getByLabelText("Email"), { target: { value: "ghost@lab.org" } });
  fireEvent.click(screen.getByRole("button", { name: /send reset link/i }));

  await waitFor(() => expect(screen.getByText(/check your email/i)).toBeInTheDocument());
});
