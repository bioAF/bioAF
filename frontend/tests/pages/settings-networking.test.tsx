import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import NetworkingSettingsPage from "@/app/settings/networking/page";

const mockPush = jest.fn();
jest.mock("next/navigation", () => ({
  usePathname: () => "/settings/networking",
  useRouter: () => ({ push: mockPush }),
}));

jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ email: "admin@bioaf.org", role: "admin", sub: "1" }),
}));

jest.mock("@/hooks/useComponents", () => ({
  useComponents: () => ({ components: [], loading: false, refetch: jest.fn() }),
}));

const mockApiGet = jest.fn();
const mockApiPut = jest.fn();
const mockApiPost = jest.fn();
jest.mock("@/lib/api", () => {
  const actual = jest.requireActual("@/lib/api");
  return {
    api: {
      get: (...args: unknown[]) => mockApiGet(...args),
      put: (...args: unknown[]) => mockApiPut(...args),
      post: (...args: unknown[]) => mockApiPost(...args),
    },
    ApiError: actual.ApiError,
    extractErrorMessage: actual.extractErrorMessage,
  };
});

type NetworkingConfigFixture = {
  hostname: string;
  domain: string;
  fqdn: string;
  reachability_status: string;
  reachability_checked_at: string | null;
  cert_status: string;
  https_enforced: boolean;
};

const blankConfig: NetworkingConfigFixture = {
  hostname: "",
  domain: "",
  fqdn: "",
  reachability_status: "",
  reachability_checked_at: null,
  cert_status: "",
  https_enforced: false,
};

const reachableConfig: NetworkingConfigFixture = {
  ...blankConfig,
  hostname: "app",
  domain: "acme.com",
  fqdn: "app.acme.com",
  reachability_status: "reachable",
  reachability_checked_at: "2026-05-26T12:00:00Z",
};

const certActiveConfig: NetworkingConfigFixture = {
  ...reachableConfig,
  cert_status: "active",
};

function setNetworkingConfig(cfg: NetworkingConfigFixture) {
  mockApiGet.mockImplementation((url: string) => {
    if (url.includes("/api/v1/settings/networking/certificate/status")) {
      return Promise.resolve({ fqdn: cfg.fqdn, status: cfg.cert_status || "not_requested" });
    }
    if (url.includes("/api/v1/settings/networking")) {
      return Promise.resolve(cfg);
    }
    // Sidebar/permissions and other unrelated calls fall through with empty data.
    return Promise.resolve({});
  });
}

describe("Networking Settings Page", () => {
  beforeEach(() => {
    mockApiGet.mockReset();
    mockApiPut.mockReset();
    mockApiPost.mockReset();
    setNetworkingConfig(blankConfig);
  });

  it("renders the three networking cards", async () => {
    render(<NetworkingSettingsPage />);
    await waitFor(() => {
      expect(screen.getByTestId("networking-hostname-card")).toBeInTheDocument();
      expect(screen.getByTestId("networking-reachability-card")).toBeInTheDocument();
      expect(screen.getByTestId("networking-tls-card")).toBeInTheDocument();
    });
  });

  it("loads existing hostname and domain from the API", async () => {
    setNetworkingConfig(reachableConfig);
    render(<NetworkingSettingsPage />);
    await waitFor(() => {
      const hostInput = screen.getByTestId("hostname-input") as HTMLInputElement;
      const domainInput = screen.getByTestId("domain-input") as HTMLInputElement;
      expect(hostInput.value).toBe("app");
      expect(domainInput.value).toBe("acme.com");
    });
    expect(mockApiGet).toHaveBeenCalledWith(
      expect.stringContaining("/api/v1/settings/networking"),
    );
  });

  it("previews the FQDN as the operator types", async () => {
    render(<NetworkingSettingsPage />);
    await waitFor(() => screen.getByTestId("hostname-input"));

    fireEvent.change(screen.getByTestId("hostname-input"), { target: { value: "lab" } });
    fireEvent.change(screen.getByTestId("domain-input"), { target: { value: "example.org" } });

    expect(screen.getByTestId("fqdn-preview")).toHaveTextContent("lab.example.org");
  });

  it("PUTs to /api/v1/settings/networking when Save is clicked", async () => {
    mockApiPut.mockResolvedValueOnce(reachableConfig);
    render(<NetworkingSettingsPage />);
    await waitFor(() => screen.getByTestId("hostname-input"));

    fireEvent.change(screen.getByTestId("hostname-input"), { target: { value: "app" } });
    fireEvent.change(screen.getByTestId("domain-input"), { target: { value: "acme.com" } });
    fireEvent.click(screen.getByTestId("save-hostname-button"));

    await waitFor(() => {
      expect(mockApiPut).toHaveBeenCalledWith(
        expect.stringContaining("/api/v1/settings/networking"),
        { hostname: "app", domain: "acme.com" },
      );
    });
  });

  it("disables the reachability test when no FQDN is configured", async () => {
    render(<NetworkingSettingsPage />);
    await waitFor(() => {
      const btn = screen.getByTestId("test-reachability-button") as HTMLButtonElement;
      expect(btn.disabled).toBe(true);
    });
  });

  it("enables and runs the reachability test once an FQDN is saved", async () => {
    setNetworkingConfig(reachableConfig);
    mockApiPost.mockResolvedValueOnce({
      fqdn: "app.acme.com",
      status: "reachable",
      detail: "",
      checked_at: "2026-05-26T12:00:00Z",
    });
    render(<NetworkingSettingsPage />);
    await waitFor(() => {
      const btn = screen.getByTestId("test-reachability-button") as HTMLButtonElement;
      expect(btn.disabled).toBe(false);
    });

    fireEvent.click(screen.getByTestId("test-reachability-button"));
    await waitFor(() => {
      expect(mockApiPost).toHaveBeenCalledWith(
        expect.stringContaining("/api/v1/settings/networking/reachability-test"),
      );
    });
  });

  it("disables the request-certificate button until reachability is verified", async () => {
    render(<NetworkingSettingsPage />);
    await waitFor(() => {
      const btn = screen.getByTestId("request-certificate-button") as HTMLButtonElement;
      expect(btn.disabled).toBe(true);
    });
  });

  it("enables the request-certificate button once reachable", async () => {
    setNetworkingConfig(reachableConfig);
    render(<NetworkingSettingsPage />);
    await waitFor(() => {
      const btn = screen.getByTestId("request-certificate-button") as HTMLButtonElement;
      expect(btn.disabled).toBe(false);
    });
  });

  it("disables the apply-https button until the certificate is active", async () => {
    setNetworkingConfig(reachableConfig);
    render(<NetworkingSettingsPage />);
    await waitFor(() => {
      const btn = screen.getByTestId("apply-https-button") as HTMLButtonElement;
      expect(btn.disabled).toBe(true);
    });
  });

  it("enables apply-https and shows a warning once the cert is active", async () => {
    setNetworkingConfig(certActiveConfig);
    render(<NetworkingSettingsPage />);
    await waitFor(() => {
      const btn = screen.getByTestId("apply-https-button") as HTMLButtonElement;
      expect(btn.disabled).toBe(false);
    });
    expect(screen.getByTestId("https-warning")).toHaveTextContent(/logged out/i);
  });

  it("polls the cert status when Refresh is clicked and surfaces a last-checked timestamp", async () => {
    const provisioningConfig = { ...reachableConfig, cert_status: "provisioning" };
    setNetworkingConfig(provisioningConfig);
    render(<NetworkingSettingsPage />);
    await waitFor(() => screen.getByTestId("refresh-cert-status-button"));

    fireEvent.click(screen.getByTestId("refresh-cert-status-button"));

    await waitFor(() => {
      expect(mockApiGet).toHaveBeenCalledWith(
        expect.stringContaining("/api/v1/settings/networking/certificate/status"),
      );
    });
    await waitFor(() => {
      expect(screen.getByTestId("cert-last-checked")).toBeInTheDocument();
    });
  });

  it("shows 'Refreshing...' on the cert refresh button while a poll is in flight", async () => {
    const provisioningConfig = { ...reachableConfig, cert_status: "provisioning" };
    setNetworkingConfig(provisioningConfig);

    // Make the certificate/status call resolve only when we release it.
    let releaseStatus: (v: { fqdn: string; status: string }) => void = () => {};
    const deferred = new Promise<{ fqdn: string; status: string }>((resolve) => {
      releaseStatus = resolve;
    });
    mockApiGet.mockImplementation((url: string) => {
      if (url.includes("/api/v1/settings/networking/certificate/status")) {
        return deferred;
      }
      if (url.includes("/api/v1/settings/networking")) {
        return Promise.resolve(provisioningConfig);
      }
      return Promise.resolve({});
    });

    render(<NetworkingSettingsPage />);
    await waitFor(() => screen.getByTestId("refresh-cert-status-button"));

    fireEvent.click(screen.getByTestId("refresh-cert-status-button"));

    await waitFor(() => {
      expect(screen.getByTestId("refresh-cert-status-button")).toHaveTextContent(
        /refreshing/i,
      );
    });

    releaseStatus({ fqdn: "app.acme.com", status: "provisioning" });
    await waitFor(() => {
      expect(screen.getByTestId("refresh-cert-status-button")).toHaveTextContent(
        /refresh status/i,
      );
    });
  });

  it("renders friendly status labels for non-reachable outcomes", async () => {
    setNetworkingConfig({
      ...reachableConfig,
      reachability_status: "dns_failed",
    });
    render(<NetworkingSettingsPage />);
    await waitFor(() => {
      expect(screen.getByTestId("reachability-status")).toHaveTextContent(
        "DNS resolution failed",
      );
    });
  });

  it("renders the detail message after a test run", async () => {
    setNetworkingConfig(reachableConfig);
    mockApiPost.mockResolvedValueOnce({
      fqdn: "app.acme.com",
      status: "dns_failed",
      detail:
        "The bioAF backend pod could not resolve app.acme.com via cluster DNS. Wait 1 to 5 minutes for negative DNS cache entries to expire and retry.",
      checked_at: "2026-05-26T12:00:00Z",
    });
    render(<NetworkingSettingsPage />);
    await waitFor(() => screen.getByTestId("test-reachability-button"));

    fireEvent.click(screen.getByTestId("test-reachability-button"));
    await waitFor(() => {
      expect(screen.getByTestId("reachability-detail")).toHaveTextContent(
        /could not resolve/i,
      );
    });
  });

  it("renders a manual-action callout (not a red error) when /certificate returns 501", async () => {
    setNetworkingConfig(reachableConfig);
    const { ApiError } = jest.requireActual("@/lib/api") as {
      ApiError: new (status: number, message: string) => Error;
    };
    mockApiPost.mockImplementation((url: string) => {
      if (url.includes("/certificate")) {
        return Promise.reject(
          new ApiError(
            501,
            "Automated certificate issuance is not yet available on VM installs. On the host, install certbot and run:\n\n    sudo certbot certonly --webroot -w /var/www/letsencrypt -d app.acme.com --email <your-email> --agree-tos --non-interactive\n\nThen copy fullchain.pem to docker/certs/tls.crt and privkey.pem to docker/certs/tls.key, and run `./bioaf restart`.",
          ),
        );
      }
      return Promise.resolve({});
    });

    render(<NetworkingSettingsPage />);
    await waitFor(() => screen.getByTestId("request-certificate-button"));
    fireEvent.click(screen.getByTestId("request-certificate-button"));

    await waitFor(() => {
      expect(screen.getByTestId("cert-manual-action")).toBeInTheDocument();
    });
    expect(screen.getByTestId("cert-manual-action")).toHaveTextContent(/certbot certonly/);
    expect(screen.queryByText(/ApiError/i)).not.toBeInTheDocument();
  });

  it("calls /enforce-https with enabled=true when Apply is clicked", async () => {
    setNetworkingConfig(certActiveConfig);
    mockApiPost.mockResolvedValueOnce({ fqdn: "app.acme.com", https_enforced: true });
    render(<NetworkingSettingsPage />);
    await waitFor(() => screen.getByTestId("apply-https-button"));

    fireEvent.click(screen.getByTestId("apply-https-button"));
    await waitFor(() => {
      expect(mockApiPost).toHaveBeenCalledWith(
        expect.stringContaining("/api/v1/settings/networking/enforce-https"),
        { enabled: true },
      );
    });
  });
});
