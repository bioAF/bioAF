import { render, screen, fireEvent, waitFor } from "@testing-library/react";

const mockGet = jest.fn();
const mockPost = jest.fn();
const mockPush = jest.fn();
let mockToken = "";

jest.mock("next/link", () => {
  return function MockLink({ children, href }: { children: React.ReactNode; href: string }) {
    return <a href={href}>{children}</a>;
  };
});

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
  useSearchParams: () => ({ get: (k: string) => (k === "token" ? mockToken : null) }),
}));

jest.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  }
  return {
    api: {
      get: (...a: unknown[]) => mockGet(...a),
      post: (...a: unknown[]) => mockPost(...a),
    },
    ApiError,
  };
});

import ResetPasswordPage from "./page";
import { ApiError } from "@/lib/api";

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
  mockPush.mockReset();
  mockToken = "tok-123";
});

test("shows an expired/invalid message and a request-new link for an invalid token", async () => {
  mockGet.mockResolvedValue({ valid: false });
  render(<ResetPasswordPage />);

  await waitFor(() => expect(screen.getByText(/expired or invalid/i)).toBeInTheDocument());
  expect(screen.getByRole("link", { name: /request a new link/i })).toHaveAttribute(
    "href",
    "/forgot-password",
  );
  expect(mockGet).toHaveBeenCalledWith(expect.stringContaining("token=tok-123"));
});

test("shows the reset form for a valid token", async () => {
  mockGet.mockResolvedValue({ valid: true });
  render(<ResetPasswordPage />);

  await waitFor(() => expect(screen.getByLabelText("Reset code")).toBeInTheDocument());
  expect(screen.getByLabelText("New password")).toBeInTheDocument();
  expect(screen.getByLabelText("Confirm new password")).toBeInTheDocument();
});

test("blocks submit and shows an error when passwords do not match", async () => {
  mockGet.mockResolvedValue({ valid: true });
  render(<ResetPasswordPage />);
  await waitFor(() => expect(screen.getByLabelText("Reset code")).toBeInTheDocument());

  fireEvent.change(screen.getByLabelText("Reset code"), { target: { value: "123456" } });
  fireEvent.change(screen.getByLabelText("New password"), { target: { value: "longenough1" } });
  fireEvent.change(screen.getByLabelText("Confirm new password"), { target: { value: "different1" } });
  fireEvent.click(screen.getByRole("button", { name: /reset password/i }));

  expect(await screen.findByText(/do not match/i)).toBeInTheDocument();
  expect(mockPost).not.toHaveBeenCalled();
});

test("submits token, code and new password, then offers a path to login", async () => {
  mockGet.mockResolvedValue({ valid: true });
  mockPost.mockResolvedValue({ message: "ok" });
  render(<ResetPasswordPage />);
  await waitFor(() => expect(screen.getByLabelText("Reset code")).toBeInTheDocument());

  fireEvent.change(screen.getByLabelText("Reset code"), { target: { value: "654321" } });
  fireEvent.change(screen.getByLabelText("New password"), { target: { value: "brandnew123" } });
  fireEvent.change(screen.getByLabelText("Confirm new password"), { target: { value: "brandnew123" } });
  fireEvent.click(screen.getByRole("button", { name: /reset password/i }));

  await waitFor(() =>
    expect(mockPost).toHaveBeenCalledWith("/api/auth/reset-password", {
      token: "tok-123",
      code: "654321",
      new_password: "brandnew123",
    }),
  );
  expect(await screen.findByText(/password reset/i)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /go to sign in/i })).toHaveAttribute("href", "/login");
});

test("surfaces a wrong-code error from the API", async () => {
  mockGet.mockResolvedValue({ valid: true });
  mockPost.mockRejectedValue(new ApiError(400, "Invalid or expired reset code"));
  render(<ResetPasswordPage />);
  await waitFor(() => expect(screen.getByLabelText("Reset code")).toBeInTheDocument());

  fireEvent.change(screen.getByLabelText("Reset code"), { target: { value: "000000" } });
  fireEvent.change(screen.getByLabelText("New password"), { target: { value: "brandnew123" } });
  fireEvent.change(screen.getByLabelText("Confirm new password"), { target: { value: "brandnew123" } });
  fireEvent.click(screen.getByRole("button", { name: /reset password/i }));

  expect(await screen.findByText(/invalid or expired reset code/i)).toBeInTheDocument();
});
