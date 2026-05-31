/**
 * Tests for the shared ComponentPicker.
 *
 * The picker is what the user sees on the new "Select Components" wizard
 * step. It is also intended to be lifted into the post-install
 * /infrastructure/components page in a later PR. Behavioural contract:
 *   - One card per component, default-checked according to props.
 *   - Dependencies auto-check when their dependents are checked.
 *   - Unchecking a dependency that has a checked dependent is not allowed
 *     (the checkbox refuses, or the dependent is auto-unchecked).
 *   - The picker emits the current set of selected keys to the parent.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ComponentPicker } from "@/components/components/ComponentPicker";

const SAMPLE_COMPONENTS = [
  {
    key: "nextflow_k8s",
    name: "Nextflow",
    description: "Pipeline orchestration using Nextflow with K8s executor.",
    category: "pipeline_orchestration",
    dependencies: ["k8s_pipeline_pool"],
    cost_estimate: "$0 (uses Kubernetes compute)",
    status: "available" as const,
  },
  {
    key: "jupyterhub",
    name: "JupyterHub",
    description: "Managed Jupyter notebooks on K8s.",
    category: "analysis",
    dependencies: ["k8s_interactive_pool"],
    cost_estimate: "$50-$200/month",
    status: "available" as const,
  },
  {
    key: "cellxgene",
    name: "cellxgene",
    description: "Interactive scRNA-seq visualization.",
    category: "visualization",
    dependencies: [],
    cost_estimate: "$20-$50/month",
    status: "available" as const,
  },
  // Always-on plumbing — should still render but the picker may render them
  // as auto-checked dependencies in a less prominent style.
  {
    key: "k8s_pipeline_pool",
    name: "K8s Pipeline Pool",
    description: "Internal node pool.",
    category: "compute",
    dependencies: [],
    cost_estimate: "$0",
    status: "available" as const,
  },
  {
    key: "k8s_interactive_pool",
    name: "K8s Interactive Pool",
    description: "Internal node pool.",
    category: "compute",
    dependencies: [],
    cost_estimate: "$0",
    status: "available" as const,
  },
];

describe("ComponentPicker", () => {
  it("renders one card per component", () => {
    render(
      <ComponentPicker
        components={SAMPLE_COMPONENTS}
        defaultSelected={[]}
        onChange={jest.fn()}
      />
    );

    expect(screen.getByText("Nextflow")).toBeInTheDocument();
    expect(screen.getByText("JupyterHub")).toBeInTheDocument();
    expect(screen.getByText("cellxgene")).toBeInTheDocument();
  });

  it("default-checks the components passed via defaultSelected", () => {
    render(
      <ComponentPicker
        components={SAMPLE_COMPONENTS}
        defaultSelected={["nextflow_k8s", "jupyterhub"]}
        onChange={jest.fn()}
      />
    );

    expect(screen.getByRole("checkbox", { name: /Nextflow/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /JupyterHub/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /cellxgene/ })).not.toBeChecked();
  });

  it("checking a component auto-checks its dependencies", async () => {
    const user = userEvent.setup();
    const onChange = jest.fn();

    render(
      <ComponentPicker
        components={SAMPLE_COMPONENTS}
        defaultSelected={[]}
        onChange={onChange}
      />
    );

    await user.click(screen.getByRole("checkbox", { name: /Nextflow/ }));

    expect(screen.getByRole("checkbox", { name: /Nextflow/ })).toBeChecked();
    expect(
      screen.getByRole("checkbox", { name: /K8s Pipeline Pool/ })
    ).toBeChecked();
    // onChange called with the dependency included
    const last = onChange.mock.calls.at(-1)?.[0] ?? [];
    expect(last).toEqual(expect.arrayContaining(["nextflow_k8s", "k8s_pipeline_pool"]));
  });

  it("unchecking a component does NOT auto-uncheck dependencies that other selected components still need", async () => {
    const user = userEvent.setup();
    const onChange = jest.fn();

    render(
      <ComponentPicker
        components={[
          ...SAMPLE_COMPONENTS,
          {
            key: "snakemake_k8s",
            name: "Snakemake",
            description: "Pipeline orchestration with snakemake.",
            category: "pipeline_orchestration",
            dependencies: ["k8s_pipeline_pool"],
            cost_estimate: "$0",
            status: "available" as const,
          },
        ]}
        defaultSelected={["nextflow_k8s", "snakemake_k8s", "k8s_pipeline_pool"]}
        onChange={onChange}
      />
    );

    await user.click(screen.getByRole("checkbox", { name: /Nextflow/ }));

    expect(screen.getByRole("checkbox", { name: /Nextflow/ })).not.toBeChecked();
    // snakemake still selected -> pool stays checked
    expect(screen.getByRole("checkbox", { name: /Snakemake/ })).toBeChecked();
    expect(
      screen.getByRole("checkbox", { name: /K8s Pipeline Pool/ })
    ).toBeChecked();
  });

  it("unchecking a component auto-unchecks dependencies that no other selected component needs", async () => {
    const user = userEvent.setup();
    const onChange = jest.fn();

    render(
      <ComponentPicker
        components={SAMPLE_COMPONENTS}
        defaultSelected={["nextflow_k8s", "k8s_pipeline_pool"]}
        onChange={onChange}
      />
    );

    await user.click(screen.getByRole("checkbox", { name: /Nextflow/ }));

    expect(screen.getByRole("checkbox", { name: /Nextflow/ })).not.toBeChecked();
    expect(
      screen.getByRole("checkbox", { name: /K8s Pipeline Pool/ })
    ).not.toBeChecked();
  });

  it("does not render coming_soon components as checkable", () => {
    render(
      <ComponentPicker
        components={[
          ...SAMPLE_COMPONENTS,
          {
            key: "snakemake_k8s",
            name: "Snakemake",
            description: "Snakemake pipeline orchestration.",
            category: "pipeline_orchestration",
            dependencies: ["k8s_pipeline_pool"],
            cost_estimate: "$0",
            status: "coming_soon" as const,
          },
        ]}
        defaultSelected={[]}
        onChange={jest.fn()}
      />
    );

    expect(screen.queryByRole("checkbox", { name: /Snakemake/ })).not.toBeInTheDocument();
    expect(screen.getByText("Snakemake")).toBeInTheDocument();
    expect(screen.getByText(/Coming Soon/i)).toBeInTheDocument();
  });
});
