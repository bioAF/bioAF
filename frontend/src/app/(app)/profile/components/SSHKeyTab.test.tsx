import { render, screen, fireEvent, waitFor } from "@/testing/renderWithProviders";

const mockGet = jest.fn();
const mockPost = jest.fn();
jest.mock("@/lib/api", () => ({
  api: { get: (...a: unknown[]) => mockGet(...a), post: (...a: unknown[]) => mockPost(...a) },
}));

import { SSHKeyTab } from "./SSHKeyTab";

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
});

test("shows the empty state and generates a key on demand", async () => {
  mockGet.mockResolvedValue({ configured: false, public_key: null });
  mockPost.mockResolvedValue({ public_key: "ssh-ed25519 AAAA...", message: "Key generated" });
  render(<SSHKeyTab />);

  await waitFor(() =>
    expect(screen.getByText(/no ssh key configured/i)).toBeInTheDocument(),
  );

  fireEvent.click(screen.getByRole("button", { name: /generate ssh key/i }));
  await waitFor(() => expect(mockPost).toHaveBeenCalledWith("/api/auth/me/ssh-key/generate"));
  expect(await screen.findByText("ssh-ed25519 AAAA...")).toBeInTheDocument();
});
