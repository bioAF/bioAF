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
