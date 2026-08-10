import { render, screen, fireEvent, waitFor } from "@testing-library/react";

const mockPatch = jest.fn();
const mockPost = jest.fn();
const mockSetToken = jest.fn();

jest.mock("@/lib/auth", () => ({
  getCurrentUser: () => ({ email: "ada@lab.org", role_name: "admin", name: "Ada" }),
  setToken: (...a: unknown[]) => mockSetToken(...a),
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
      patch: (...a: unknown[]) => mockPatch(...a),
      post: (...a: unknown[]) => mockPost(...a),
    },
    ApiError,
  };
});

import { AccountTab } from "./AccountTab";

beforeEach(() => {
  mockPatch.mockReset();
  mockPost.mockReset();
  mockSetToken.mockReset();
});

test("shows the user's email and role", () => {
  render(<AccountTab />);
  expect(screen.getByText("ada@lab.org")).toBeInTheDocument();
  expect(screen.getByText("admin")).toBeInTheDocument();
});

test("saves a new name, refreshes the token, and announces the update", async () => {
  mockPatch.mockResolvedValue({});
  mockPost.mockResolvedValue({ access_token: "new.jwt.token" });
  render(<AccountTab />);

  fireEvent.change(screen.getByLabelText("Display name"), { target: { value: "Ada Lovelace" } });
  fireEvent.click(screen.getByRole("button", { name: /save name/i }));

  await waitFor(() => expect(mockPatch).toHaveBeenCalledWith("/api/auth/me", { name: "Ada Lovelace" }));
  await waitFor(() => expect(mockPost).toHaveBeenCalledWith("/api/auth/refresh"));
  expect(mockSetToken).toHaveBeenCalledWith("new.jwt.token");
  expect(await screen.findByText(/name updated/i)).toBeInTheDocument();
});

test("rejects an empty name without calling the API", async () => {
  render(<AccountTab />);
  fireEvent.change(screen.getByLabelText("Display name"), { target: { value: "   " } });
  fireEvent.click(screen.getByRole("button", { name: /save name/i }));

  expect(await screen.findByText(/name cannot be empty/i)).toBeInTheDocument();
  expect(mockPatch).not.toHaveBeenCalled();
});

test("blocks a password change when the new passwords do not match", async () => {
  render(<AccountTab />);
  fireEvent.change(screen.getByLabelText("Current password"), { target: { value: "oldpass123" } });
  fireEvent.change(screen.getByLabelText("New password"), { target: { value: "newpass123" } });
  fireEvent.change(screen.getByLabelText("Confirm new password"), { target: { value: "different123" } });
  fireEvent.click(screen.getByRole("button", { name: /change password/i }));

  expect(await screen.findByText(/do not match/i)).toBeInTheDocument();
  expect(mockPost).not.toHaveBeenCalled();
});
