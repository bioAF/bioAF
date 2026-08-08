import { isChildActive, NavChild, navConfig } from "./navConfig";

const projectChildren: NavChild[] = [
  { label: "Project List", path: "/projects" },
  { label: "Experiment Templates", path: "/projects/experiment-templates" },
  { label: "Experiment List", path: "/projects/experiments" },
];

describe("isChildActive", () => {
  it("matches exact path", () => {
    expect(isChildActive("/projects", projectChildren[0], projectChildren)).toBe(true);
    expect(isChildActive("/projects/experiments", projectChildren[2], projectChildren)).toBe(true);
  });

  it("does not highlight Project List when on Experiment List", () => {
    expect(isChildActive("/projects/experiments", projectChildren[0], projectChildren)).toBe(false);
  });

  it("does not highlight Project List when on Experiment Templates", () => {
    expect(isChildActive("/projects/experiment-templates", projectChildren[0], projectChildren)).toBe(false);
  });

  it("highlights Project List for subpages like /projects/123", () => {
    expect(isChildActive("/projects/123", projectChildren[0], projectChildren)).toBe(true);
  });

  it("highlights Experiment List for subpages like /projects/experiments/456", () => {
    expect(isChildActive("/projects/experiments/456", projectChildren[2], projectChildren)).toBe(true);
  });

  it("does not match unrelated paths", () => {
    expect(isChildActive("/pipelines/runs", projectChildren[0], projectChildren)).toBe(false);
  });
});

const allChildren = (): NavChild[] => navConfig.flatMap((s) => s.children ?? []);

describe("navConfig disambiguated labels", () => {
  it("renames the two 'Environments' entries so each says what it is", () => {
    // Both used to be called "Environments". The first fix qualified them as
    // "Pipeline Environments" and "Compute Environments"; the owner went further
    // on 2026-08-08 ("the 'Environments' moniker has created a lot of
    // confusion") and the workbench one is "Workbench Images" now, in the nav and
    // in its own page heading. The property is unchanged and stronger: neither
    // entry can be read as the other.
    const pipelines = navConfig.find((s) => s.label === "Pipelines");
    const workbench = navConfig.find((s) => s.label === "Workbench");

    expect(pipelines?.children?.find((c) => c.label === "Pipeline Environments")?.path).toBe(
      "/pipelines/environments",
    );
    expect(workbench?.children?.find((c) => c.label === "Workbench Images")?.path).toBe(
      "/environments",
    );
  });

  it("has no bare 'Environments' label left to collide", () => {
    expect(allChildren().some((c) => c.label === "Environments")).toBe(false);
  });

  it("has no two nav children sharing the same label", () => {
    const labels = allChildren().map((c) => c.label);
    const duplicates = labels.filter((l, i) => labels.indexOf(l) !== i);
    expect(duplicates).toEqual([]);
  });
});

describe("Experiments surfaced as a top-level section", () => {
  it("renames the 'Projects' section to 'Experiments'", () => {
    expect(navConfig.some((s) => s.label === "Projects")).toBe(false);
    expect(navConfig.some((s) => s.label === "Experiments")).toBe(true);
  });

  it("keeps the same sub-menu items under the renamed section", () => {
    const exp = navConfig.find((s) => s.label === "Experiments");
    expect(exp?.children?.map((c) => c.path)).toEqual([
      "/projects",
      "/projects/experiment-templates",
      "/projects/experiments",
      "/data/browser",
    ]);
  });
});

describe("Validation Studies nav entry", () => {
  it("adds a Validation Studies child under Lab Knowledge gated on lit_validation:view", () => {
    const labKnowledge = navConfig.find((s) => s.label === "Lab Knowledge");
    const child = labKnowledge?.children?.find((c) => c.label === "Validation Studies");
    expect(child?.path).toBe("/lab-knowledge/validation-studies");
    expect(child?.permission).toEqual({ resource: "lit_validation", action: "view" });
    expect(child?.betaFlag).toBe("lit_validation");
  });

  it("no longer lists Literature or Validation Studies under Data & Files", () => {
    const data = navConfig.find((s) => s.label === "Data & Files");
    const labels = data?.children?.map((c) => c.label) ?? [];
    expect(labels).not.toContain("Literature");
    expect(labels).not.toContain("Validation Studies");
  });
});

describe("top-level nav order", () => {
  it("lists sections in the intended order", () => {
    expect(navConfig.map((s) => s.label)).toEqual([
      "Dashboard",
      "Experiments",
      "Pipelines",
      "Results",
      "Workbench",
      "Data & Files",
      "Lab Knowledge",
      "Infrastructure",
      "Settings",
    ]);
  });
});
