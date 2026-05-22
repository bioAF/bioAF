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
    const pipelines = navConfig.find((s) => s.label === "Pipelines");
    const workbench = navConfig.find((s) => s.label === "Workbench");

    expect(pipelines?.children?.find((c) => c.label === "Pipeline Environments")?.path).toBe(
      "/pipelines/environments",
    );
    expect(workbench?.children?.find((c) => c.label === "Compute Environments")?.path).toBe(
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

describe("top-level nav order", () => {
  it("lists sections in the intended order", () => {
    expect(navConfig.map((s) => s.label)).toEqual([
      "Dashboard",
      "Experiments",
      "Pipelines",
      "Results",
      "Workbench",
      "Data & Files",
      "Profile",
      "Infrastructure",
      "Settings",
    ]);
  });
});
