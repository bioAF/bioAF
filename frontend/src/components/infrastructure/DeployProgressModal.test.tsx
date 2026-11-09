/**
 * A failed progress read used to be swallowed, so the modal kept showing the last state as if it
 * were current. It now says the progress could not be read.
 */
import { render, screen } from "@testing-library/react";
import { DeployProgressModal } from "./DeployProgressModal";

const base = {
  phase: "compute",
  status: "applying",
  resourcesCompleted: 2,
  resourcesTotal: 4,
  completedResources: [],
  plannedResources: [],
  errorMessage: null,
  onDismiss: () => {},
  onAbort: () => {},
  onDone: () => {},
};

test("says when progress could not be read, keeping the last known state", () => {
  render(<DeployProgressModal {...base} progressUnavailable />);
  expect(screen.getByTestId("deploy-modal-progress-unavailable")).toHaveTextContent(
    /could not be read/i,
  );
  expect(screen.getByText(/Applying changes/)).toBeInTheDocument();
});

test("says nothing extra while progress reads succeed", () => {
  render(<DeployProgressModal {...base} />);
  expect(screen.queryByTestId("deploy-modal-progress-unavailable")).not.toBeInTheDocument();
});
