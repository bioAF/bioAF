import { isChildActive, NavChild } from "./navConfig";

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
