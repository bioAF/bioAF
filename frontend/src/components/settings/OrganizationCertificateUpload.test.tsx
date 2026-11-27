/**
 * Plan 03.5A: a private installation installs a certificate from its organization's own CA.
 * Proposed wording, pending owner sign-off.
 */
import { render, screen, fireEvent, waitFor } from "@/testing/renderWithProviders";
import { OrganizationCertificateUpload } from "./OrganizationCertificateUpload";

jest.mock("@/lib/api", () => {
  const actual = jest.requireActual("@/lib/api");
  return { ...actual, api: { post: jest.fn() } };
});
import { api, ApiError } from "@/lib/api";
const mockPost = api.post as jest.Mock;

beforeEach(() => {
  mockPost.mockReset();
  jest.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => jest.restoreAllMocks());

function fill(cert: string, key: string) {
  fireEvent.change(screen.getByLabelText("Server certificate (PEM)"), { target: { value: cert } });
  fireEvent.change(screen.getByLabelText("Private key (PEM)"), { target: { value: key } });
}

test("uploading sends both PEM texts and says what the certificate covers", async () => {
  mockPost.mockResolvedValue({ status: "installing", names: ["bioaf.lab.internal"], not_after: "2027-01-01T00:00:00Z", issuer: "CN=Lab Root CA" });
  render(<OrganizationCertificateUpload />);
  fill("-----BEGIN CERTIFICATE-----x", "-----BEGIN PRIVATE KEY-----y");
  fireEvent.click(screen.getByRole("button", { name: "Upload certificate" }));
  await waitFor(() =>
    expect(mockPost).toHaveBeenCalledWith("/api/v1/settings/networking/certificate/upload", {
      certificate_pem: "-----BEGIN CERTIFICATE-----x",
      private_key_pem: "-----BEGIN PRIVATE KEY-----y",
    }),
  );
  expect(await screen.findByText(/Installing the certificate for bioaf\.lab\.internal/)).toBeInTheDocument();
  // The key is cleared from the page once it is sent.
  expect((screen.getByLabelText("Private key (PEM)") as HTMLTextAreaElement).value).toBe("");
});

test("a refused upload shows the plain reason and keeps the certificate text", async () => {
  mockPost.mockRejectedValue(new ApiError(422, "The private key does not match the certificate."));
  render(<OrganizationCertificateUpload />);
  fill("CERT", "KEY");
  fireEvent.click(screen.getByRole("button", { name: "Upload certificate" }));
  expect(await screen.findByText("The private key does not match the certificate.")).toBeInTheDocument();
  expect((screen.getByLabelText("Server certificate (PEM)") as HTMLTextAreaElement).value).toBe("CERT");
});

test("the button waits for both texts", () => {
  render(<OrganizationCertificateUpload />);
  expect(screen.getByRole("button", { name: "Upload certificate" })).toBeDisabled();
});
