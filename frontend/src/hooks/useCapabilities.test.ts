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

import { useCapabilities, clearCapabilitiesCache } from "./useCapabilities";

beforeEach(() => {
  mockApiGet.mockReset();
  clearCapabilitiesCache();
});

describe("useCapabilities", () => {
  it("fetches the active capability set from the bootstrap status endpoint", async () => {
    mockApiGet.mockResolvedValue({
      setup_complete: true,
      has_setup_code: false,
      has_admin: true,
      capabilities: {
        cost_estimation: true,
        autoscaling: true,
        ssh_exec: true,
        spot_retry: true,
        job_report: true,
        signed_url_upload: true,
        storage_tier_metrics: true,
        notebooks: true,
        cellxgene: true,
        work_nodes: true,
        messaging: false,
        billing: false,
      },
    });

    const { result } = renderHook(() => useCapabilities());

    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(mockApiGet).toHaveBeenCalledWith("/api/bootstrap/status");
    expect(result.current.has("cost_estimation")).toBe(true);
    expect(result.current.has("signed_url_upload")).toBe(true);
    expect(result.current.has("messaging")).toBe(false);
    expect(result.current.has("billing")).toBe(false);
  });

  it("reflects an incapable backend: false flags gate their controls off", async () => {
    mockApiGet.mockResolvedValue({
      setup_complete: true,
      has_setup_code: false,
      has_admin: true,
      capabilities: {
        cost_estimation: false,
        autoscaling: false,
        ssh_exec: false,
        spot_retry: false,
        job_report: false,
        signed_url_upload: false,
        storage_tier_metrics: false,
        notebooks: true,
        cellxgene: false,
        work_nodes: false,
        messaging: false,
        billing: false,
      },
    });

    const { result } = renderHook(() => useCapabilities());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.has("cost_estimation")).toBe(false);
    expect(result.current.has("autoscaling")).toBe(false);
    expect(result.current.has("signed_url_upload")).toBe(false);
    expect(result.current.has("notebooks")).toBe(true);
  });

  it("fails safe to the minimal set (everything hidden) when the fetch errors", async () => {
    mockApiGet.mockRejectedValue(new Error("network down"));

    const { result } = renderHook(() => useCapabilities());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.has("cost_estimation")).toBe(false);
    expect(result.current.has("notebooks")).toBe(false);
    expect(result.current.has("work_nodes")).toBe(false);
  });

  it("fails safe when the response omits the capabilities payload", async () => {
    mockApiGet.mockResolvedValue({
      setup_complete: true,
      has_setup_code: false,
      has_admin: true,
    });

    const { result } = renderHook(() => useCapabilities());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.has("cost_estimation")).toBe(false);
    expect(result.current.has("cellxgene")).toBe(false);
  });
});
