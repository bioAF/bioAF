/**
 * Plan 03.5A: an installation without a hostname (public IP-only, or private with an organization
 * certificate) sees its installed certificate in Settings: expiry, who renews it, and the last
 * renewal attempt, from the host's report (owner-approved copy, 2026-11-26).
 */
import { render, screen } from "@/testing/renderWithProviders";
import { formatRecordedTime } from "@/lib/recordedTime";

const get = jest.fn();
jest.mock("@/lib/api", () => {
  class ApiError extends Error {
    status = 0;
  }
  return { api: { get: (...a: unknown[]) => get(...a), post: jest.fn(), put: jest.fn() }, ApiError };
});

import { NetworkingSettingsContent } from "./NetworkingSettingsContent";

const NO_HOSTNAME = {
  hostname: "",
  domain: "",
  fqdn: "",
  reachability_status: "",
  reachability_checked_at: null,
  cert_status: "not_requested",
  https_enforced: false,
};

const EXPIRES = "2026-12-02T12:00:00Z";
const CHECKED = "2026-11-26T03:00:00Z";

function serve(certificate: object, config: object = NO_HOSTNAME) {
  get.mockImplementation((url: string) =>
    Promise.resolve(url.endsWith("/certificate/status") ? certificate : config),
  );
}

beforeEach(() => get.mockReset());

test("a certificate bioAF renews shows its expiry and the last renewal check", async () => {
  serve({
    fqdn: "",
    status: "active",
    not_after: EXPIRES,
    issuer: "Let's Encrypt",
    renewed_by_bioaf: true,
    last_renewal_at: CHECKED,
    last_renewal_result: "succeeded",
  });
  render(<NetworkingSettingsContent />);
  const card = await screen.findByTestId("installed-certificate");
  expect(card).toHaveTextContent("Installed certificate");
  expect(card).toHaveTextContent("active");
  expect(card).toHaveTextContent(`Expires ${formatRecordedTime(EXPIRES, "monthDayYearTime")}`);
  expect(card).toHaveTextContent("Renewed automatically by bioAF.");
  expect(card).toHaveTextContent(`Last renewal check: ${formatRecordedTime(CHECKED, "monthDayTime")} (succeeded)`);
  expect(screen.queryByTestId("certificate-renewal-failed")).not.toBeInTheDocument();
});

test("a failed renewal says the certificate keeps working and bioAF retries", async () => {
  serve({
    fqdn: "",
    status: "active",
    not_after: EXPIRES,
    issuer: "Let's Encrypt",
    renewed_by_bioaf: true,
    last_renewal_at: CHECKED,
    last_renewal_result: "failed",
  });
  render(<NetworkingSettingsContent />);
  const warning = await screen.findByTestId("certificate-renewal-failed");
  expect(warning).toHaveTextContent(
    "The last renewal attempt failed. The current certificate keeps working until it expires. bioAF tries again every six hours.",
  );
});

test("an uploaded certificate says bioAF does not renew it", async () => {
  serve({
    fqdn: "",
    status: "active",
    not_after: EXPIRES,
    issuer: "Lab CA",
    renewed_by_bioaf: false,
    last_renewal_at: null,
    last_renewal_result: null,
  });
  render(<NetworkingSettingsContent />);
  const card = await screen.findByTestId("installed-certificate");
  expect(card).toHaveTextContent("Not renewed by bioAF. Upload a new certificate before it expires.");
  expect(card).not.toHaveTextContent("Last renewal check");
});

test("without a reported certificate nothing extra is shown", async () => {
  serve({ fqdn: "", status: "not_requested" });
  render(<NetworkingSettingsContent />);
  await screen.findByTestId("networking-tls-card");
  expect(get).toHaveBeenCalledWith("/api/v1/settings/networking/certificate/status");
  expect(screen.queryByTestId("installed-certificate")).not.toBeInTheDocument();
});

test("an installation with a hostname keeps its managed-certificate flow", async () => {
  serve({ fqdn: "a.b.org", status: "active" }, { ...NO_HOSTNAME, hostname: "a", domain: "b.org", fqdn: "a.b.org" });
  render(<NetworkingSettingsContent />);
  await screen.findByTestId("networking-tls-card");
  expect(get).not.toHaveBeenCalledWith("/api/v1/settings/networking/certificate/status");
  expect(screen.queryByTestId("installed-certificate")).not.toBeInTheDocument();
});
