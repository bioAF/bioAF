/**
 * Tests 21-23: StorageSection component (Steps 10-11 - Phase 18).
 *
 * 21: Shows deploy card when storage_deployed is false
 * 22: Shows bucket cards when deployed
 * 23: Ingest bucket has guidance panel with copy buttons
 */

import { render, screen, waitFor } from "@testing-library/react";
import { StorageSection } from "@/components/components/StorageSection";

const mockApiGet = jest.fn();
jest.mock("@/lib/api", () => ({
  api: { get: (...args: unknown[]) => mockApiGet(...args) },
}));

// Mock next/link
jest.mock("next/link", () => ({
  __esModule: true,
  default: ({
    href,
    children,
  }: {
    href: string;
    children: React.ReactNode;
  }) => <a href={href}>{children}</a>,
}));

describe("StorageSection", () => {
  beforeEach(() => {
    mockApiGet.mockReset();
  });

  it("shows deploy card when storage is not deployed", async () => {
    render(
      <StorageSection
        storageDeployed={false}
        terraformInitialized={true}
        onDeploy={jest.fn()}
      />
    );

    expect(screen.getByText(/Deploy Storage/i)).toBeInTheDocument();
    expect(
      screen.getByText(/has not been deployed/i)
    ).toBeInTheDocument();
  });

  it("shows bucket cards when deployed with live data", async () => {
    const mockBuckets = [
      {
        bucket_name: "bioaf-ingest-demo",
        purpose: "ingest",
        size_bytes: 1024,
        object_count: 5,
        storage_class: "STANDARD",
        versioning_enabled: true,
        lifecycle_rules: [],
        created_at: null,
      },
      {
        bucket_name: "bioaf-raw-demo",
        purpose: "raw",
        size_bytes: 2048,
        object_count: 10,
        storage_class: "STANDARD",
        versioning_enabled: true,
        lifecycle_rules: ["Transition to NEARLINE after 90 days"],
        created_at: null,
      },
      {
        bucket_name: "bioaf-working-demo",
        purpose: "working",
        size_bytes: 512,
        object_count: 3,
        storage_class: "STANDARD",
        versioning_enabled: true,
        lifecycle_rules: [],
        created_at: null,
      },
      {
        bucket_name: "bioaf-results-demo",
        purpose: "results",
        size_bytes: 0,
        object_count: 0,
        storage_class: "STANDARD",
        versioning_enabled: true,
        lifecycle_rules: [],
        created_at: null,
      },
      {
        bucket_name: "bioaf-references-demo",
        purpose: "references",
        size_bytes: 4096,
        object_count: 12,
        storage_class: "STANDARD",
        versioning_enabled: true,
        lifecycle_rules: [],
        created_at: null,
      },
      {
        bucket_name: "bioaf-literature-demo",
        purpose: "literature",
        size_bytes: 8192,
        object_count: 8,
        storage_class: "STANDARD",
        versioning_enabled: true,
        lifecycle_rules: [],
        created_at: null,
      },
      {
        bucket_name: "bioaf-config-backups-demo",
        purpose: "config_backups",
        size_bytes: 256,
        object_count: 2,
        storage_class: "STANDARD",
        versioning_enabled: true,
        lifecycle_rules: [],
        created_at: null,
      },
    ];

    mockApiGet.mockResolvedValue({ buckets: mockBuckets });

    render(
      <StorageSection
        storageDeployed={true}
        terraformInitialized={true}
        onDeploy={jest.fn()}
      />
    );

    await waitFor(() => {
      // Ingest bucket name appears multiple times (heading + guidance panel)
      expect(screen.getAllByText("bioaf-ingest-demo").length).toBeGreaterThan(0);
      expect(screen.getByText("bioaf-raw-demo")).toBeInTheDocument();
      expect(screen.getByText("bioaf-working-demo")).toBeInTheDocument();
      expect(screen.getByText("bioaf-results-demo")).toBeInTheDocument();
      expect(screen.getByText("bioaf-references-demo")).toBeInTheDocument();
      expect(screen.getByText("bioaf-literature-demo")).toBeInTheDocument();
      expect(
        screen.getByText("bioaf-config-backups-demo")
      ).toBeInTheDocument();
    });
  });

  it("ingest bucket card shows guidance panel with copy buttons", async () => {
    const mockBuckets = [
      {
        bucket_name: "bioaf-ingest-demo",
        purpose: "ingest",
        size_bytes: 0,
        object_count: 0,
        storage_class: "STANDARD",
        versioning_enabled: true,
        lifecycle_rules: [],
        created_at: null,
      },
      {
        bucket_name: "bioaf-raw-demo",
        purpose: "raw",
        size_bytes: 0,
        object_count: 0,
        storage_class: "STANDARD",
        versioning_enabled: true,
        lifecycle_rules: [],
        created_at: null,
      },
      {
        bucket_name: "bioaf-working-demo",
        purpose: "working",
        size_bytes: 0,
        object_count: 0,
        storage_class: "STANDARD",
        versioning_enabled: true,
        lifecycle_rules: [],
        created_at: null,
      },
      {
        bucket_name: "bioaf-results-demo",
        purpose: "results",
        size_bytes: 0,
        object_count: 0,
        storage_class: "STANDARD",
        versioning_enabled: true,
        lifecycle_rules: [],
        created_at: null,
      },
      {
        bucket_name: "bioaf-config-backups-demo",
        purpose: "config_backups",
        size_bytes: 0,
        object_count: 0,
        storage_class: "STANDARD",
        versioning_enabled: true,
        lifecycle_rules: [],
        created_at: null,
      },
    ];

    mockApiGet.mockResolvedValue({ buckets: mockBuckets });

    render(
      <StorageSection
        storageDeployed={true}
        terraformInitialized={true}
        onDeploy={jest.fn()}
      />
    );

    await waitFor(() => {
      expect(screen.getByText(/gsutil cp/)).toBeInTheDocument();
      expect(
        screen.getAllByText(/bioaf-ingest-demo/).length
      ).toBeGreaterThan(0);
    });

    // Should have copy buttons
    const copyButtons = screen.getAllByLabelText("Copy");
    expect(copyButtons.length).toBeGreaterThan(0);
  });

  it("disables deploy button when terraform is not initialized", () => {
    render(
      <StorageSection
        storageDeployed={false}
        terraformInitialized={false}
        onDeploy={jest.fn()}
      />
    );

    const button = screen.getByRole("button", { name: /Deploy Storage/i });
    expect(button).toBeDisabled();
  });
});
