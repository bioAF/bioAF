import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SetupWizard } from "@/components/auth/SetupWizard";

// Mock fetch globally
const mockFetch = jest.fn();
global.fetch = mockFetch;

// Mock next/navigation
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
}));

function mockFetchResponse(status: number, body: unknown) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  });
}

beforeEach(() => {
  mockFetch.mockReset();
  localStorage.clear();
});

async function advanceToStep(user: ReturnType<typeof userEvent.setup>, targetStep: number) {
  // Step 0 -> 1: verify setup code
  if (targetStep >= 1) {
    mockFetch.mockImplementationOnce(() =>
      mockFetchResponse(200, { setup_token: "fake-jwt", message: "Setup code verified" })
    );
    await user.type(screen.getByPlaceholderText("Enter 6-character code"), "ABC123");
    await user.click(screen.getByRole("button", { name: /verify/i }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "Create Admin Account" })).toBeInTheDocument());
  }

  // Step 1 -> 2: create admin
  if (targetStep >= 2) {
    mockFetch.mockImplementationOnce(() =>
      mockFetchResponse(200, { access_token: "admin-jwt", token_type: "bearer", message: "ok" })
    );
    await user.type(screen.getByLabelText(/name/i), "Admin");
    await user.type(screen.getByLabelText(/email/i), "admin@test.com");
    await user.type(screen.getByLabelText(/^password$/i), "password123");
    await user.type(screen.getByLabelText(/confirm password/i), "password123");
    await user.click(screen.getByRole("button", { name: /create admin/i }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "Organization Name" })).toBeInTheDocument());
  }

  // Step 2 -> 3: org name
  if (targetStep >= 3) {
    mockFetch.mockImplementationOnce(() => mockFetchResponse(200, { message: "ok" }));
    await user.type(screen.getByLabelText(/organization name/i), "Test Org");
    await user.click(screen.getByRole("button", { name: /save organization/i }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "GCP Credentials" })).toBeInTheDocument());
  }

  // Step 3 -> 4: skip GCP
  if (targetStep >= 4) {
    await user.click(screen.getByRole("button", { name: /do this later/i }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "SMTP Settings" })).toBeInTheDocument());
  }

  // Step 4 -> 5: skip SMTP
  if (targetStep >= 5) {
    await user.click(screen.getByRole("button", { name: /do this later/i }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "Infrastructure" })).toBeInTheDocument());
  }
}

describe("SetupWizard", () => {
  const onComplete = jest.fn();

  it("shows setup code input on step 0", () => {
    render(<SetupWizard onComplete={onComplete} />);
    expect(screen.getByRole("heading", { name: "Setup Code" })).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Enter 6-character code")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /verify/i })).toBeInTheDocument();
  });

  it("advances to admin creation on valid code", async () => {
    const user = userEvent.setup();
    render(<SetupWizard onComplete={onComplete} />);

    mockFetch.mockImplementationOnce(() =>
      mockFetchResponse(200, { setup_token: "fake-jwt", message: "Setup code verified" })
    );

    await user.type(screen.getByPlaceholderText("Enter 6-character code"), "ABC123");
    await user.click(screen.getByRole("button", { name: /verify/i }));

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Create Admin Account" })).toBeInTheDocument();
    });
  });

  it("shows error on invalid code", async () => {
    const user = userEvent.setup();
    render(<SetupWizard onComplete={onComplete} />);

    mockFetch.mockImplementationOnce(() =>
      mockFetchResponse(401, { detail: "Invalid or expired setup code" })
    );

    await user.type(screen.getByPlaceholderText("Enter 6-character code"), "ZZZZZZ");
    await user.click(screen.getByRole("button", { name: /verify/i }));

    await waitFor(() => {
      expect(screen.getByText(/invalid or expired/i)).toBeInTheDocument();
    });
  });

  it("shows name, email, password fields on step 1 (no verification code)", async () => {
    const user = userEvent.setup();
    render(<SetupWizard onComplete={onComplete} />);
    await advanceToStep(user, 1);

    expect(screen.getByLabelText(/name/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/email/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/^password$/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/confirm password/i)).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/verification/i)).not.toBeInTheDocument();
  });

  it('shows "Do this later" on steps 3, 4, 5', async () => {
    const user = userEvent.setup();
    render(<SetupWizard onComplete={onComplete} />);
    await advanceToStep(user, 3);

    // Step 3: GCP -- "Do this later" visible, not "Skip for now"
    expect(screen.getByRole("button", { name: /do this later/i })).toBeInTheDocument();
    expect(screen.queryByText(/skip for now/i)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /do this later/i }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "SMTP Settings" })).toBeInTheDocument());

    // Step 4: SMTP -- "Do this later"
    expect(screen.getByRole("button", { name: /do this later/i })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /do this later/i }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "Infrastructure" })).toBeInTheDocument());

    // Step 5: Infrastructure -- "Do this later"
    expect(screen.getByRole("button", { name: /do this later/i })).toBeInTheDocument();
  });

  it("disables infrastructure button when GCP was skipped", async () => {
    const user = userEvent.setup();
    render(<SetupWizard onComplete={onComplete} />);
    await advanceToStep(user, 5);

    const infraButton = screen.getByRole("button", { name: /set up infrastructure/i });
    expect(infraButton).toBeDisabled();
    expect(screen.getByText(/GCP credentials are required/i)).toBeInTheDocument();
  });
});
