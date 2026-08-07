import { renderHook } from "@testing-library/react";

const mockPermissions = jest.fn();
jest.mock("@/hooks/usePermissions", () => ({ usePermissions: () => mockPermissions() }));
const mockComponents = jest.fn();
jest.mock("@/hooks/useComponents", () => ({ useComponents: () => mockComponents() }));
const mockCapabilities = jest.fn();
jest.mock("@/hooks/useCapabilities", () => ({ useCapabilities: () => mockCapabilities() }));
const mockBeta = jest.fn();
jest.mock("@/hooks/useBetaFeatures", () => ({ useBetaFeatures: () => mockBeta() }));

import { useVisibleNavSections } from "./useVisibleNavSections";

function component(key: string, category: string, enabled: boolean) {
  return { key, name: key, description: "", category, enabled, status: "ready", config: {}, dependencies: [], estimated_monthly_cost: "", updated_at: null };
}

beforeEach(() => {
  mockPermissions.mockReturnValue({ canAccess: () => true, roleName: "admin", loading: false });
  mockComponents.mockReturnValue({ components: [component("nextflow_k8s", "pipeline_orchestration", true)], loading: false });
  mockCapabilities.mockReturnValue({ has: () => true });
  mockBeta.mockReturnValue({ available: false, flags: {} });
});

test("returns nothing at all while permissions are still loading", () => {
  mockPermissions.mockReturnValue({ canAccess: () => true, roleName: "", loading: true });
  const { result } = renderHook(() => useVisibleNavSections());
  expect(result.current.sections).toEqual([]);
  expect(result.current.loading).toBe(true);
});

test("gives back the sections a permitted user can see", () => {
  const { result } = renderHook(() => useVisibleNavSections());
  const labels = result.current.sections.map((s) => s.label);
  expect(labels).toContain("Settings");
  expect(labels).toContain("Lab Knowledge");
});

test("drops a child the user has no permission for", () => {
  mockPermissions.mockReturnValue({
    canAccess: (resource: string) => resource !== "users",
    roleName: "admin",
    loading: false,
  });
  const { result } = renderHook(() => useVisibleNavSections());
  const settings = result.current.sections.find((s) => s.label === "Settings");
  expect(settings!.children!.map((c) => c.label)).not.toContain("Users & Accounts");
});

test("drops a beta child while its flag is off", () => {
  const { result } = renderHook(() => useVisibleNavSections());
  const knowledge = result.current.sections.find((s) => s.label === "Lab Knowledge");
  expect(knowledge!.children!.map((c) => c.label)).not.toContain("Validation Studies");
});

test("keeps a beta child once its flag is on", () => {
  mockBeta.mockReturnValue({ available: true, flags: { lit_validation: true } });
  const { result } = renderHook(() => useVisibleNavSections());
  const knowledge = result.current.sections.find((s) => s.label === "Lab Knowledge");
  expect(knowledge!.children!.map((c) => c.label)).toContain("Validation Studies");
});

test("drops a child whose backend capability is absent", () => {
  mockCapabilities.mockReturnValue({ has: (flag: string) => flag !== "work_nodes" });
  const { result } = renderHook(() => useVisibleNavSections());
  const settings = result.current.sections.find((s) => s.label === "Settings");
  expect(settings!.children!.map((c) => c.label)).not.toContain("Workbench Settings");
});

test("names the first place a section can actually take you", () => {
  const { result } = renderHook(() => useVisibleNavSections());
  expect(result.current.firstChildPath("Settings")).toBe("/settings/users");
  expect(result.current.firstChildPath("Infrastructure")).toBe("/infrastructure/components");
  expect(result.current.firstChildPath("Lab Knowledge")).toBe("/lab-knowledge/documents");
});

test("a section the user cannot reach at all has no destination", () => {
  mockPermissions.mockReturnValue({ canAccess: () => false, roleName: "viewer", loading: false });
  const { result } = renderHook(() => useVisibleNavSections());
  expect(result.current.firstChildPath("Settings")).toBeNull();
});
