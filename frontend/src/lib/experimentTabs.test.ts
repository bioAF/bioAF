import { resolveExperimentTab } from "./experimentTabs";

describe("resolveExperimentTab", () => {
  it("returns the requested tab when it is valid", () => {
    expect(resolveExperimentTab("files")).toBe("files");
    expect(resolveExperimentTab("samples")).toBe("samples");
    expect(resolveExperimentTab("agent_review")).toBe("agent_review");
  });

  it("falls back to overview for missing or unknown tabs", () => {
    expect(resolveExperimentTab(null)).toBe("overview");
    expect(resolveExperimentTab(undefined)).toBe("overview");
    expect(resolveExperimentTab("")).toBe("overview");
    expect(resolveExperimentTab("not-a-tab")).toBe("overview");
  });
});
