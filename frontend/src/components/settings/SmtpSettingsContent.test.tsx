import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { SmtpSettingsContent } from "./SmtpSettingsContent";

jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn() },
}));

import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;
const mockPost = api.post as jest.Mock;

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
  mockPost.mockResolvedValue({});
  mockGet.mockResolvedValue({
    host: "smtp.example.com",
    port: 587,
    username: "user@example.com",
    // The API returns the password MASKED; there is no real value to populate.
    password: "or***et",
    from_address: "noreply@example.com",
    encryption: "starttls",
    configured: true,
  });
});

test("a save that did not touch the password omits it, so the stored one survives", async () => {
  render(<SmtpSettingsContent />);
  await waitFor(() => expect(screen.getByDisplayValue("smtp.example.com")).toBeInTheDocument());

  // Admin edits only the From Address.
  fireEvent.change(screen.getByDisplayValue("noreply@example.com"), {
    target: { value: "alerts@example.com" },
  });
  fireEvent.click(screen.getByRole("button", { name: /save smtp/i }));

  await waitFor(() => expect(mockPost).toHaveBeenCalled());
  const body = mockPost.mock.calls[0][1];
  expect(body.from_address).toBe("alerts@example.com");
  // Sending "" here wiped the stored credential and silently broke all outbound
  // email. Omitting the key tells the backend to keep what it has.
  expect(body.password).toBeUndefined();
});

test("a save that DID set a password sends it", async () => {
  render(<SmtpSettingsContent />);
  await waitFor(() => expect(screen.getByDisplayValue("smtp.example.com")).toBeInTheDocument());

  fireEvent.change(screen.getByPlaceholderText(/saved|Enter password/i), {
    target: { value: "brand-new-secret" },
  });
  fireEvent.click(screen.getByRole("button", { name: /save smtp/i }));

  await waitFor(() => expect(mockPost).toHaveBeenCalled());
  expect(mockPost.mock.calls[0][1].password).toBe("brand-new-secret");
});

test("tells the admin that leaving the password blank keeps the stored one", async () => {
  render(<SmtpSettingsContent />);
  await waitFor(() => expect(screen.getByDisplayValue("smtp.example.com")).toBeInTheDocument());
  expect(screen.getByText(/leave blank to keep/i)).toBeInTheDocument();
});
