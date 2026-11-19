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
  mockPermissions.mockReturnValue({ canAccess: () => true, roleName: "admin", loading: false, failed: false });
  mockComponents.mockReturnValue({ components: [component("nextflow_k8s", "pipeline_orchestration", true)], loading: false, failed: false });
  mockCapabilities.mockReturnValue({ has: () => true });
  mockBeta.mockReturnValue({ flags: {} });
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
  mockBeta.mockReturnValue({ flags: { lit_validation: true } });
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

test("hides a component-gated section when the component really is not installed", () => {
  mockComponents.mockReturnValue({
    components: [component("nextflow_k8s", "pipeline_orchestration", false)],
    loading: false,
    failed: false,
  });
  const { result } = renderHook(() => useVisibleNavSections());
  expect(result.current.sections.map((s) => s.label)).not.toContain("Pipelines");
});

/**
 * Measured live on the deployed app: a 500 on /api/v1/infrastructure/stack/components
 * removed the entire Pipelines section from the sidebar, with no error anywhere
 * on screen, so the user concluded the feature was not installed.
 *
 * A failed check is not a negative answer. The gate already declines to hide
 * anything while components are LOADING, for exactly this reason; a failure is
 * the same state of ignorance and gets the same treatment. The page behind the
 * link reports its own error if the feature really is absent, which is a far
 * better outcome than a silently shorter menu.
 */
test("keeps a component-gated section visible when the component check failed", () => {
  mockComponents.mockReturnValue({ components: [], loading: false, failed: true });
  const { result } = renderHook(() => useVisibleNavSections());
  expect(result.current.sections.map((s) => s.label)).toContain("Pipelines");
});

test("keeps component-gated children visible when the component check failed", () => {
  mockComponents.mockReturnValue({ components: [], loading: false, failed: true });
  const { result } = renderHook(() => useVisibleNavSections());
  const results = result.current.sections.find((s) => s.label === "Results");
  expect(results!.children!.map((c) => c.label)).toContain("QC Dashboards");
});

/**
 * Permissions are the one gate that must NOT open on failure: granting what we
 * cannot verify is a security defect. Instead the hook reports the failure so
 * the shell can say so rather than rendering an empty account.
 */
test("reports a permission-load failure instead of returning a usable empty nav", () => {
  mockPermissions.mockReturnValue({
    canAccess: () => false,
    roleName: "",
    loading: false,
    failed: true,
  });
  const { result } = renderHook(() => useVisibleNavSections());
  expect(result.current.permissionsFailed).toBe(true);
});

/**
 * The Work Nodes link follows the permission its page and API actually check:
 * `work_nodes:view`. Notebook visibility is a different grant. The built-in
 * bench role has `notebooks:view` without `work_nodes:view`, and a link that
 * only bounces the user back to the dashboard is not discoverability.
 */
describe("Work Nodes navigation follows work_nodes:view", () => {
  function grant(...keys: string[]) {
    const granted = new Set(keys);
    mockPermissions.mockReturnValue({
      canAccess: (resource: string, action: string) => granted.has(`${resource}:${action}`),
      roleName: "custom",
      loading: false,
      failed: false,
    });
  }

  function workbenchChildren(sections: { label: string; children?: { label: string }[] }[]) {
    const workbench = sections.find((s) => s.label === "Workbench");
    return workbench ? workbench.children!.map((c) => c.label) : null;
  }

  test.each([
    { name: "Work Nodes view only", keys: ["work_nodes:view"], visible: true },
    { name: "notebooks view only (the bench pattern)", keys: ["notebooks:view", "environments:view"], visible: false },
    { name: "both views", keys: ["work_nodes:view", "notebooks:view"], visible: true },
    { name: "neither view", keys: ["environments:view"], visible: false },
  ])("$name: Work Nodes link visible is $visible", ({ keys, visible }) => {
    grant(...keys);
    const { result } = renderHook(() => useVisibleNavSections());
    const children = workbenchChildren(result.current.sections) ?? [];
    if (visible) expect(children).toContain("Work Nodes");
    else expect(children).not.toContain("Work Nodes");
  });

  test("hides Work Nodes when the provider lacks the work_nodes capability, even with view permission", () => {
    grant("work_nodes:view", "environments:view");
    mockCapabilities.mockReturnValue({ has: (flag: string) => flag !== "work_nodes" });
    const { result } = renderHook(() => useVisibleNavSections());
    expect(workbenchChildren(result.current.sections)).not.toContain("Work Nodes");
  });

  test("keeps Workbench and lands on Work Nodes when it is the only eligible child", () => {
    grant("work_nodes:view");
    const { result } = renderHook(() => useVisibleNavSections());
    expect(workbenchChildren(result.current.sections)).toEqual(["Work Nodes"]);
    expect(result.current.firstChildPath("Workbench")).toBe("/workbench/work-nodes");
  });

  test("keeps Workbench through an eligible sibling when Work Nodes is hidden", () => {
    grant("notebooks:view", "environments:view");
    const { result } = renderHook(() => useVisibleNavSections());
    expect(workbenchChildren(result.current.sections)).toEqual(["Workbench Templates"]);
    expect(result.current.firstChildPath("Workbench")).toBe("/environments");
  });

  test("drops Workbench entirely when no child is eligible", () => {
    grant("notebooks:view");
    const { result } = renderHook(() => useVisibleNavSections());
    expect(workbenchChildren(result.current.sections)).toBeNull();
    expect(result.current.firstChildPath("Workbench")).toBeNull();
  });
});
