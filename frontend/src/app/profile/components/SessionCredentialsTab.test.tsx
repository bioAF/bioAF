import { render, screen, fireEvent, waitFor } from "@testing-library/react";

const mockGet = jest.fn();
const mockPut = jest.fn();
jest.mock("@/lib/api", () => ({
  api: { get: (...a: unknown[]) => mockGet(...a), put: (...a: unknown[]) => mockPut(...a) },
  ApiError: class ApiError extends Error {},
}));
jest.mock("@/components/shared/ContentLoading", () => ({ ContentLoading: () => <div /> }));

import { SessionCredentialsTab } from "./SessionCredentialsTab";

beforeEach(() => {
  mockGet.mockReset();
  mockPut.mockReset();
});

test("shows the not-configured state and validates matching passwords", async () => {
  mockGet.mockResolvedValue({ configured: false, username: null, created_at: null, updated_at: null });
  render(<SessionCredentialsTab />);

  await waitFor(() => expect(screen.getByText(/no session credentials set/i)).toBeInTheDocument());

  fireEvent.click(screen.getByRole("button", { name: /set up/i }));
  fireEvent.change(screen.getByPlaceholderText("Choose a password"), { target: { value: "rstudio123" } });
  fireEvent.change(screen.getByPlaceholderText("Confirm your password"), { target: { value: "mismatch123" } });
  fireEvent.click(screen.getByRole("button", { name: /set up credentials/i }));

  expect(await screen.findByText(/do not match/i)).toBeInTheDocument();
  expect(mockPut).not.toHaveBeenCalled();
});

test("saves credentials when the passwords match", async () => {
  mockGet.mockResolvedValue({ configured: false, username: null, created_at: null, updated_at: null });
  mockPut.mockResolvedValue({ configured: true, username: "ada", created_at: null, updated_at: null });
  render(<SessionCredentialsTab />);

  await waitFor(() => expect(screen.getByText(/no session credentials set/i)).toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: /set up/i }));
  fireEvent.change(screen.getByPlaceholderText("Choose a password"), { target: { value: "rstudio123" } });
  fireEvent.change(screen.getByPlaceholderText("Confirm your password"), { target: { value: "rstudio123" } });
  fireEvent.click(screen.getByRole("button", { name: /set up credentials/i }));

  await waitFor(() =>
    expect(mockPut).toHaveBeenCalledWith(
      "/api/auth/me/session-credentials",
      expect.objectContaining({ password: "rstudio123" }),
    ),
  );
});
