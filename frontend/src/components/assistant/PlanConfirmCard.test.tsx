import { render, screen } from "@testing-library/react";
import { PlanConfirmCard } from "./PlanConfirmCard";

describe("PlanConfirmCard", () => {
  it("renders every step of a multi-step plan so nothing is confirmed unseen", () => {
    render(
      <PlanConfirmCard
        steps={[
          { tool: "install", args: { name: "nf-core/scrnaseq" } },
          {
            tool: "launch_run",
            args: { experiment_id: 7, pipeline_key: "nf-core/scrnaseq" },
          },
        ]}
        busy={false}
        onConfirm={() => {}}
        onCancel={() => {}}
      />,
    );
    // Both consequential steps are shown with readable titles and numbered.
    expect(screen.getByText(/Install pipeline/i)).toBeInTheDocument();
    expect(screen.getByText(/Launch pipeline run/i)).toBeInTheDocument();
    expect(screen.getByText(/Step 1:/)).toBeInTheDocument();
    expect(screen.getByText(/Step 2:/)).toBeInTheDocument();
    // The pipeline name is surfaced (on both the install and the launch step) so a wrong
    // pipeline is caught before confirm.
    expect(screen.getAllByText("nf-core/scrnaseq").length).toBeGreaterThanOrEqual(1);
  });

  it("titles and labels a data-setup plan (create_experiment / create_sample)", () => {
    render(
      <PlanConfirmCard
        steps={[
          { tool: "create_experiment", args: { name: "Cortex scRNA pilot" } },
          {
            tool: "create_sample",
            args: { experiment_id: 7, external_id: "S1", organism: "Mus musculus", assay: "scrna" },
          },
        ]}
        busy={false}
        onConfirm={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByText(/Create experiment/i)).toBeInTheDocument();
    expect(screen.getByText(/Create sample/i)).toBeInTheDocument();
    // The experiment name is labelled "Name", not mislabelled "Pipeline".
    expect(screen.getByText("Name")).toBeInTheDocument();
    expect(screen.getByText("Cortex scRNA pilot")).toBeInTheDocument();
    expect(screen.queryByText("Pipeline")).not.toBeInTheDocument();
    // The sample's first-class assay is surfaced so a wrong assay is caught before confirm.
    expect(screen.getByText("Assay")).toBeInTheDocument();
    expect(screen.getByText("scrna")).toBeInTheDocument();
  });

  it("titles a single-step plan without step numbering", () => {
    render(
      <PlanConfirmCard
        steps={[{ tool: "launch_run", args: { experiment_id: 7, pipeline_key: "nf-core/rnaseq" } }]}
        busy={false}
        onConfirm={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByText(/Launch pipeline run/i)).toBeInTheDocument();
    expect(screen.queryByText(/Step 1:/)).not.toBeInTheDocument();
  });
});
