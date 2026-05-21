import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { StorageSection } from "@/components/components/StorageSection";

const mockApiGet = jest.fn();
jest.mock("@/lib/api", () => ({
  api: {
    get: (...args: unknown[]) => mockApiGet(...args),
  },
}));

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

// Mock clipboard
Object.assign(navigator, {
  clipboard: {
    writeText: jest.fn().mockResolvedValue(undefined),
  },
});

const bucketsResponse = {
  buckets: [
    {
      bucket_name: "bioaf-ingest-test-org",
      purpose: "ingest",
      size_bytes: 0,
      object_count: 0,
      storage_class: "STANDARD",
      versioning_enabled: true,
      lifecycle_rules: [],
      created_at: null,
    },
    {
      bucket_name: "bioaf-raw-test-org",
      purpose: "raw",
      size_bytes: 2684354560,
      object_count: 45,
      storage_class: "STANDARD",
      versioning_enabled: true,
      lifecycle_rules: ["Transition to NEARLINE after 90 days"],
      created_at: null,
    },
    {
      bucket_name: "bioaf-working-test-org",
      purpose: "working",
      size_bytes: 1288490189,
      object_count: 120,
      storage_class: "STANDARD",
      versioning_enabled: true,
      lifecycle_rules: [],
      created_at: null,
    },
    {
      bucket_name: "bioaf-results-test-org",
      purpose: "results",
      size_bytes: 858993459,
      object_count: 35,
      storage_class: "STANDARD",
      versioning_enabled: true,
      lifecycle_rules: [],
      created_at: null,
    },
    {
      bucket_name: "bioaf-references-test-org",
      purpose: "references",
      size_bytes: 524288000,
      object_count: 12,
      storage_class: "STANDARD",
      versioning_enabled: true,
      lifecycle_rules: [],
      created_at: null,
    },
    {
      bucket_name: "bioaf-literature-test-org",
      purpose: "literature",
      size_bytes: 134217728,
      object_count: 8,
      storage_class: "STANDARD",
      versioning_enabled: true,
      lifecycle_rules: [],
      created_at: null,
    },
    {
      bucket_name: "bioaf-config-backups-test-org",
      purpose: "config_backups",
      size_bytes: 10737418,
      object_count: 5,
      storage_class: "NEARLINE",
      versioning_enabled: true,
      lifecycle_rules: [],
      created_at: null,
    },
  ],
};

describe("StorageSection", () => {
  beforeEach(() => {
    mockApiGet.mockReset();
    mockApiGet.mockResolvedValue(bucketsResponse);
  });

  it("renders all bucket cards when deployed", async () => {
    render(
      <StorageSection
        storageDeployed={true}
        terraformInitialized={true}
        onDeploy={jest.fn()}
      />
    );
    await waitFor(() => {
      // Ingest appears multiple times (heading + guidance)
      expect(screen.getAllByText("bioaf-ingest-test-org").length).toBeGreaterThan(0);
      expect(screen.getByText("bioaf-raw-test-org")).toBeInTheDocument();
      expect(screen.getByText("bioaf-working-test-org")).toBeInTheDocument();
      expect(screen.getByText("bioaf-results-test-org")).toBeInTheDocument();
      expect(screen.getByText("bioaf-references-test-org")).toBeInTheDocument();
      expect(screen.getByText("bioaf-literature-test-org")).toBeInTheDocument();
      expect(screen.getByText("bioaf-config-backups-test-org")).toBeInTheDocument();
    });
  });

  it("ingest bucket card has distinct styling", async () => {
    render(
      <StorageSection
        storageDeployed={true}
        terraformInitialized={true}
        onDeploy={jest.fn()}
      />
    );
    await waitFor(() => {
      const ingestCard = screen.getByTestId("bucket-card-bioaf-ingest-test-org");
      expect(ingestCard.className).toMatch(/border-teal|border-blue|ring-teal|ring-blue/);
    });
  });

  it("ingest bucket guidance panel contains gsutil command", async () => {
    render(
      <StorageSection
        storageDeployed={true}
        terraformInitialized={true}
        onDeploy={jest.fn()}
      />
    );
    await waitFor(() => {
      expect(
        screen.getByText(/gsutil cp .* gs:\/\/bioaf-ingest-test-org\//),
      ).toBeInTheDocument();
    });
  });

  it("copy button copies bucket name to clipboard", async () => {
    render(
      <StorageSection
        storageDeployed={true}
        terraformInitialized={true}
        onDeploy={jest.fn()}
      />
    );
    await waitFor(() => {
      expect(screen.getAllByRole("button", { name: /copy/i }).length).toBeGreaterThan(0);
    });
    const copyButtons = screen.getAllByRole("button", { name: /copy/i });
    fireEvent.click(copyButtons[0]);
    expect(navigator.clipboard.writeText).toHaveBeenCalled();
  });

  it("link to naming profiles navigates to /settings/naming-profiles", async () => {
    render(
      <StorageSection
        storageDeployed={true}
        terraformInitialized={true}
        onDeploy={jest.fn()}
      />
    );
    await waitFor(() => {
      const namingLink = screen.getByText(/naming profiles/i).closest("a");
      expect(namingLink).toHaveAttribute("href", "/settings/naming-profiles");
    });
  });

  it("link to dataset browser navigates to /data/browser", async () => {
    render(
      <StorageSection
        storageDeployed={true}
        terraformInitialized={true}
        onDeploy={jest.fn()}
      />
    );
    await waitFor(() => {
      const dataLink = screen.getByText(/view ingested files/i).closest("a");
      expect(dataLink).toHaveAttribute("href", "/data/browser");
    });
  });

  it("fetches from /api/v1/infrastructure/storage/buckets when deployed", async () => {
    render(
      <StorageSection
        storageDeployed={true}
        terraformInitialized={true}
        onDeploy={jest.fn()}
      />
    );
    await waitFor(() => {
      expect(mockApiGet).toHaveBeenCalledWith("/api/v1/infrastructure/storage/buckets");
    });
  });

  it("shows deploy card when not deployed", () => {
    render(
      <StorageSection
        storageDeployed={false}
        terraformInitialized={true}
        onDeploy={jest.fn()}
      />
    );
    expect(screen.getByText(/has not been deployed/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Deploy Storage/ })).toBeInTheDocument();
  });
});
