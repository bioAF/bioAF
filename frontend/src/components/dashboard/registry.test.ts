jest.mock("@/lib/api", () => ({ api: {} }));

import {
  getWidget,
  canUseWidget,
  accessibleWidgets,
  defaultLayoutForRole,
  type WidgetDefinition,
} from "./registry";

describe("widget registry", () => {
  test("getWidget returns a definition for a known key, undefined otherwise", () => {
    expect(getWidget("experiments_status")?.key).toBe("experiments_status");
    expect(getWidget("does_not_exist")).toBeUndefined();
  });

  test("comp_bio defaults are pipeline widgets, not cost", () => {
    const keys = defaultLayoutForRole("comp_bio");
    expect(keys).toContain("active_pipeline_runs");
    expect(keys).toContain("queue_depth");
    expect(keys).not.toContain("cost_budget");
  });

  test("admin defaults include cost and infra", () => {
    const keys = defaultLayoutForRole("admin");
    expect(keys).toContain("cost_budget");
    expect(keys).toContain("infra_health");
  });

  test("bench defaults include experiments status", () => {
    expect(defaultLayoutForRole("bench")).toContain("experiments_status");
  });

  test("an unknown/custom role falls back to experiments_status only", () => {
    expect(defaultLayoutForRole("some_custom_role")).toEqual(["experiments_status"]);
  });

  test("accessibleWidgets filters by permission (ANY semantics)", () => {
    const canAccess = (r: string, a: string) => r === "experiments" && a === "view";
    const keys = accessibleWidgets(canAccess).map((w) => w.key);
    // experiments:view grants the experiment-scoped widgets...
    expect(keys).toEqual(expect.arrayContaining(["experiments_status", "recent_plots"]));
    // ...but nothing requiring other resources
    expect(keys).not.toContain("active_pipeline_runs");
    expect(keys).not.toContain("cost_budget");
    expect(keys).not.toContain("recent_literature");
  });

  test("canUseWidget is true when any required permission matches", () => {
    const def = getWidget("queue_depth") as WidgetDefinition;
    expect(canUseWidget(def, (r, a) => r === "pipelines" && a === "view")).toBe(true);
    expect(canUseWidget(def, () => false)).toBe(false);
  });
});
