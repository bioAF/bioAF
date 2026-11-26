import { render, screen, fireEvent, waitFor } from "@/testing/renderWithProviders";

const mockGet = jest.fn();
const mockPost = jest.fn();
const mockDownload = jest.fn();
jest.mock("@/lib/api", () => ({
  api: {
    get: (...a: unknown[]) => mockGet(...a),
    post: (...a: unknown[]) => mockPost(...a),
    download: (...a: unknown[]) => mockDownload(...a),
  },
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
}));

import { SSHKeyTab } from "./SSHKeyTab";

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
  mockDownload.mockReset();
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


// Plan 03B (D03-6): the same key is the user's Work Node login key.
test("a configured key offers Download key with local permissions and the ssh -i command", async () => {
  mockGet.mockResolvedValue({ configured: true, public_key: "ssh-rsa AAAA a@b.c" });
  mockDownload.mockResolvedValue(undefined);
  render(<SSHKeyTab />);
  const button = await screen.findByRole("button", { name: "Download key" });
  expect(screen.getByText(/chmod 600 ~\/.ssh\/bioaf_id_rsa/)).toBeInTheDocument();
  expect(screen.getByText(/ssh -i ~\/.ssh\/bioaf_id_rsa/)).toBeInTheDocument();
  expect(screen.getByText(/also gives access to your GitHub repositories/i)).toBeInTheDocument();
  fireEvent.click(button);
  await waitFor(() => expect(mockDownload).toHaveBeenCalledWith("/api/auth/me/ssh-key/private"));
});

test("a replacement refused because work nodes still need the key shows the plain reason and keeps the key", async () => {
  const { ApiError } = jest.requireMock("@/lib/api");
  mockGet.mockResolvedValue({ configured: true, public_key: "ssh-rsa AAAA a@b.c" });
  mockPost.mockRejectedValue(
    new ApiError(409, "Finish and stop your current work nodes before replacing this key. You can download your current key again."),
  );
  render(<SSHKeyTab />);
  fireEvent.click(await screen.findByRole("button", { name: /regenerate key/i }));
  fireEvent.click(await screen.findByRole("button", { name: "Replace" }));
  expect(
    await screen.findByText(
      "Finish and stop your current work nodes before replacing this key. You can download your current key again.",
    ),
  ).toBeInTheDocument();
  expect(screen.getByText("ssh-rsa AAAA a@b.c")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Download key" })).toBeInTheDocument();
});

test("the replacement warning says running work nodes keep the old key and GitHub needs the new one", async () => {
  mockGet.mockResolvedValue({ configured: true, public_key: "ssh-rsa AAAA a@b.c" });
  render(<SSHKeyTab />);
  fireEvent.click(await screen.findByRole("button", { name: /regenerate key/i }));
  expect(await screen.findByText(/add the new public key to GitHub/i)).toBeInTheDocument();
});
