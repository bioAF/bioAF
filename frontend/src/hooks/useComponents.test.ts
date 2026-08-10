import { renderHook, waitFor } from "@testing-library/react";

const mockApiGet = jest.fn();
jest.mock("@/lib/api", () => ({
  api: { get: (...args: unknown[]) => mockApiGet(...args) },
}));

import { useComponents, invalidateComponentCache } from "./useComponents";

const payload = {
  compute_stack: "nextflow_k8s",
  compute_deployed: true,
  storage_deployed: true,
  components: [
    {
      key: "nextflow_k8s",
      name: "Nextflow on Kubernetes",
      category: "pipeline_orchestration",
      description: "",
      cost_estimate: "",
      dependencies: [],
      status: "enabled",
      configurable: true,
    },
  ],
};

beforeEach(() => {
  mockApiGet.mockReset();
  invalidateComponentCache();
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => jest.restoreAllMocks());

describe("useComponents", () => {
  it("maps the installed components and reports no failure", async () => {
    mockApiGet.mockResolvedValue(payload);
    const { result } = renderHook(() => useComponents());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.components).toHaveLength(1);
    expect(result.current.components[0].enabled).toBe(true);
    expect(result.current.failed).toBe(false);
  });

  /**
   * The defect this locks: a failed fetch resolved to `components: []` with
   * `loading: false`, which every componentGate reads as "the feature is not
   * installed". Measured live: a 500 on this one endpoint removed the whole
   * Pipelines section from the sidebar, with no error anywhere on screen.
   * "We could not check" has to be distinguishable from "it is not installed".
   */
  it("says the fetch failed instead of reporting an empty component set", async () => {
    mockApiGet.mockRejectedValue(new Error("stack components down"));
    const { result } = renderHook(() => useComponents());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.failed).toBe(true);
    expect(result.current.components).toEqual([]);
  });

  it("puts the real error in the logs", async () => {
    mockApiGet.mockRejectedValue(new Error("stack components down"));
    const { result } = renderHook(() => useComponents());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(console.error).toHaveBeenCalledWith(
      expect.stringContaining("[bioAF]"),
      expect.any(Error),
    );
  });

  /**
   * A failure was cached at module level, so one transient blip locked the tab
   * into a degraded nav for its whole lifetime. Only a success is worth caching.
   */
  it("does not cache a failure, so the next mount tries again", async () => {
    mockApiGet.mockRejectedValue(new Error("stack components down"));
    const first = renderHook(() => useComponents());
    await waitFor(() => expect(first.result.current.loading).toBe(false));
    expect(first.result.current.failed).toBe(true);
    first.unmount();

    mockApiGet.mockResolvedValue(payload);
    const second = renderHook(() => useComponents());
    await waitFor(() => expect(second.result.current.loading).toBe(false));

    expect(second.result.current.failed).toBe(false);
    expect(second.result.current.components).toHaveLength(1);
  });

  it("still caches a success, so navigation does not refetch", async () => {
    mockApiGet.mockResolvedValue(payload);
    const first = renderHook(() => useComponents());
    await waitFor(() => expect(first.result.current.loading).toBe(false));
    first.unmount();

    const callsAfterFirst = mockApiGet.mock.calls.length;
    const second = renderHook(() => useComponents());
    await waitFor(() => expect(second.result.current.loading).toBe(false));

    expect(mockApiGet.mock.calls.length).toBe(callsAfterFirst);
    expect(second.result.current.components).toHaveLength(1);
  });
});
