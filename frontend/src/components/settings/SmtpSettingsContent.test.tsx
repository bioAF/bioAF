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

// `catch { // Settings not configured yet }` assumed every failure meant "nothing
// stored yet". It also caught 500s and dropped connections, and the form then rendered
// blank with port 587 and starttls, which are component defaults, not the org's
// settings. Save posts host/username/from_address unconditionally, so one click wrote
// empty strings over a working mail configuration. The password was already protected
// by the omit-unless-typed rule above; the other four fields were not.
describe("when the stored settings cannot be read", () => {
  beforeEach(() => {
    jest.spyOn(console, "error").mockImplementation(() => {});
  });
  afterEach(() => {
    (console.error as jest.Mock).mockRestore?.();
  });

  test("says so, instead of showing a blank form that looks unconfigured", async () => {
    mockGet.mockRejectedValue(new Error("HTTP 500"));
    render(<SmtpSettingsContent />);

    expect(await screen.findByTestId("smtp-load-failed")).toHaveTextContent(/could not be loaded/i);
  });

  test("cannot be saved, because saving would blank what was not read", async () => {
    mockGet.mockRejectedValue(new Error("HTTP 500"));
    render(<SmtpSettingsContent />);
    await screen.findByTestId("smtp-load-failed");

    const save = screen.getByRole("button", { name: /save smtp/i });
    expect(save).toBeDisabled();
    fireEvent.click(save);
    expect(mockPost).not.toHaveBeenCalled();
  });

  test("puts the real error in the logs", async () => {
    mockGet.mockRejectedValue(new Error("HTTP 500"));
    render(<SmtpSettingsContent />);
    await screen.findByTestId("smtp-load-failed");

    expect(console.error).toHaveBeenCalledWith(
      expect.stringContaining("SMTP settings"),
      expect.any(Error)
    );
  });

  test("a 404 still means 'nothing configured yet', and stays saveable", async () => {
    // The original comment was right about ONE case. A first-run instance has no
    // settings row, and that must remain an editable empty form rather than an error.
    mockGet.mockRejectedValue(Object.assign(new Error("Not Found"), { status: 404 }));
    render(<SmtpSettingsContent />);

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /save smtp/i })).not.toBeDisabled()
    );
    expect(screen.queryByTestId("smtp-load-failed")).not.toBeInTheDocument();
  });
});
