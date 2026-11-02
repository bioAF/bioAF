import { act, renderHook, waitFor } from "@testing-library/react";
import { useProjectOptions } from "./useProjectOptions";

jest.mock("@/lib/api", () => ({ api: { get: jest.fn() } }));
import { api } from "@/lib/api";
const mockGet = api.get as jest.Mock;

const denied = Object.assign(new Error("role_missing"), { status: 403 });

beforeEach(() => {
  mockGet.mockReset();
  jest.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => jest.restoreAllMocks());

test("reports loading, then the projects", async () => {
  mockGet.mockResolvedValue({ projects: [{ id: 1, name: "Atlas" }] });
  const { result } = renderHook(() => useProjectOptions());
  expect(result.current.status).toBe("loading");
  await waitFor(() => expect(result.current.status).toBe("loaded"));
  expect(result.current.projects).toEqual([{ id: 1, name: "Atlas" }]);
});

test("reads the path it is given", async () => {
  mockGet.mockResolvedValue({ projects: [] });
  renderHook(() => useProjectOptions({ path: "/api/projects?page_size=200" }));
  await waitFor(() => expect(mockGet).toHaveBeenCalledWith("/api/projects?page_size=200"));
});

test("keeps denial and failure apart", async () => {
  mockGet.mockRejectedValueOnce(denied);
  const first = renderHook(() => useProjectOptions());
  await waitFor(() => expect(first.result.current.status).toBe("denied"));

  mockGet.mockRejectedValueOnce(new Error("Backend unavailable"));
  const second = renderHook(() => useProjectOptions());
  await waitFor(() => expect(second.result.current.status).toBe("failed"));
});

test("does not read until enabled", async () => {
  mockGet.mockResolvedValue({ projects: [] });
  const { rerender } = renderHook(({ enabled }) => useProjectOptions({ enabled }), {
    initialProps: { enabled: false },
  });
  expect(mockGet).not.toHaveBeenCalled();
  rerender({ enabled: true });
  await waitFor(() => expect(mockGet).toHaveBeenCalled());
});

test("a retry that succeeds replaces the failure", async () => {
  mockGet.mockRejectedValueOnce(new Error("Backend unavailable"));
  const { result } = renderHook(() => useProjectOptions());
  await waitFor(() => expect(result.current.status).toBe("failed"));

  mockGet.mockResolvedValueOnce({ projects: [{ id: 1, name: "Atlas" }] });
  act(() => result.current.retry());
  await waitFor(() => expect(result.current.status).toBe("loaded"));
});

test("hands back the same list across renders while loading", () => {
  mockGet.mockReturnValue(new Promise(() => {}));
  const { result, rerender } = renderHook(() => useProjectOptions());
  const first = result.current.projects;
  rerender();
  expect(result.current.projects).toBe(first);
});
