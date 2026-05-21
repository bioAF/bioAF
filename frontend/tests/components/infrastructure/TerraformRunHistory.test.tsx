/**
 * Test 33: TerraformRunHistory component (Step 13 - Phase 17).
 *
 * 33: Run history renders recent operations table with run data
 */

import { render, screen } from "@testing-library/react";
import { TerraformRunHistory } from "@/components/infrastructure/TerraformRunHistory";

const mockRuns = [
  {
    id: 1,
    action: "bootstrap",
    module_name: "foundation",
    status: "completed",
    resources_planned: 1,
    resources_completed: 1,
    triggered_by_user_id: 1,
    started_at: "2026-03-11T00:00:00Z",
    completed_at: "2026-03-11T00:01:00Z",
    error_message: null,
    plan_json: null,
    terraform_state_url: null,
  },
  {
    id: 2,
    action: "plan",
    module_name: "foundation",
    status: "failed",
    resources_planned: 0,
    resources_completed: 0,
    triggered_by_user_id: 1,
    started_at: "2026-03-11T00:02:00Z",
    completed_at: "2026-03-11T00:02:30Z",
    error_message: "terraform init failed",
    plan_json: null,
    terraform_state_url: null,
  },
];

test("TerraformRunHistory renders recent operations table", () => {
  render(<TerraformRunHistory runs={mockRuns} />);

  expect(screen.getByTestId("run-history-table")).toBeInTheDocument();
  expect(screen.getByText("bootstrap")).toBeInTheDocument();
  expect(screen.getByText("plan")).toBeInTheDocument();
});

test("TerraformRunHistory shows status for each run", () => {
  render(<TerraformRunHistory runs={mockRuns} />);

  expect(screen.getByText("completed")).toBeInTheDocument();
  expect(screen.getByText("failed")).toBeInTheDocument();
});

test("TerraformRunHistory shows empty state when no runs", () => {
  render(<TerraformRunHistory runs={[]} />);

  expect(screen.getByTestId("run-history-empty")).toBeInTheDocument();
});

function cancelledRun(overrides: Partial<(typeof mockRuns)[number]>) {
  return {
    id: 99,
    action: "plan",
    module_name: "compute",
    status: "cancelled",
    resources_planned: 0,
    resources_completed: 0,
    triggered_by_user_id: 1,
    started_at: "2026-05-21T08:51:57Z",
    completed_at: "2026-05-21T08:51:58Z",
    error_message: null,
    plan_json: null,
    terraform_state_url: null,
    ...overrides,
  };
}

test("cancelled plan with no planned changes reads 'No changes'", () => {
  render(<TerraformRunHistory runs={[cancelledRun({ resources_planned: 0 })]} />);

  expect(screen.getByText("No changes")).toBeInTheDocument();
  expect(screen.queryByText("cancelled")).not.toBeInTheDocument();
});

test("cancelled plan that found but did not apply changes reads 'Not applied'", () => {
  render(<TerraformRunHistory runs={[cancelledRun({ resources_planned: 1 })]} />);

  expect(screen.getByText("Not applied")).toBeInTheDocument();
});

test("user-abandoned run shows the reason as a tooltip", () => {
  render(
    <TerraformRunHistory
      runs={[cancelledRun({ action: "apply", error_message: "Abandoned by user" })]}
    />,
  );

  const badge = screen.getByText("Cancelled");
  expect(badge).toHaveAttribute("title", "Abandoned by user");
});
