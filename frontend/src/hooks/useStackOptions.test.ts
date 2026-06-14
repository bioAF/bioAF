import { renderHook, waitFor } from "@testing-library/react";

const mockApiGet = jest.fn();
jest.mock("@/lib/api", () => ({
  api: {
    get: (...args: unknown[]) => mockApiGet(...args),
  },
}));

jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
}));

import {
  useStackOptions,
  invalidateStackOptionsCache,
  DEFAULT_STACK_OPTIONS,
} from "./useStackOptions";

beforeEach(() => {
  mockApiGet.mockReset();
  invalidateStackOptionsCache();
});

describe("useStackOptions", () => {
  it("fetches the install's stack options from the endpoint", async () => {
    mockApiGet.mockResolvedValue({
      cloud_provider: "gcp",
      options: DEFAULT_STACK_OPTIONS.options,
    });

    const { result } = renderHook(() => useStackOptions());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(mockApiGet).toHaveBeenCalledWith("/api/v1/infrastructure/stack-options");
    expect(result.current.cloudProvider).toBe("gcp");
    expect(result.current.kubernetesOption?.label).toBe("Kubernetes + GCS");
    expect(result.current.kubernetesOption?.compute_label).toBe("Kubernetes (GKE)");
  });

  it("reflects an AWS install: EKS + S3 labels", async () => {
    mockApiGet.mockResolvedValue({
      cloud_provider: "aws",
      options: [
        {
          compute_stack: "kubernetes",
          storage_backend: "s3",
          label: "Kubernetes + S3",
          compute_label: "Kubernetes (EKS)",
          storage_label: "S3",
          available: true,
          recommended: true,
        },
        {
          compute_stack: "slurm",
          storage_backend: "nfs",
          label: "SLURM + NFS",
          compute_label: "SLURM",
          storage_label: "NFS",
          available: false,
          recommended: false,
        },
      ],
    });

    const { result } = renderHook(() => useStackOptions());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.cloudProvider).toBe("aws");
    expect(result.current.kubernetesOption?.label).toBe("Kubernetes + S3");
    expect(result.current.kubernetesOption?.compute_label).toBe("Kubernetes (EKS)");
    expect(result.current.kubernetesOption?.storage_label).toBe("S3");
  });

  it("fails safe to GCP defaults when the fetch errors (GCP renders unchanged)", async () => {
    mockApiGet.mockRejectedValue(new Error("network down"));

    const { result } = renderHook(() => useStackOptions());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.cloudProvider).toBe("gcp");
    expect(result.current.kubernetesOption?.label).toBe("Kubernetes + GCS");
  });
});
