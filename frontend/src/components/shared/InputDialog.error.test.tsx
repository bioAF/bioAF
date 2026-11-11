import { render, screen } from "@testing-library/react";
import { InputDialog } from "./InputDialog";

test("an error in the prompt is announced and describes the field", () => {
  render(<InputDialog open title="Dismiss" label="Reason" error="The paper could not be dismissed." onConfirm={() => {}} onCancel={() => {}} />);
  expect(screen.getByRole("alert")).toHaveTextContent("The paper could not be dismissed.");
  expect(screen.getByLabelText("Reason")).toHaveAccessibleDescription("The paper could not be dismissed.");
  expect(screen.getByLabelText("Reason")).toHaveAttribute("aria-invalid", "true");
});

test("without an error there is no alert", () => {
  render(<InputDialog open title="Dismiss" label="Reason" onConfirm={() => {}} onCancel={() => {}} />);
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.getByLabelText("Reason")).not.toHaveAttribute("aria-invalid");
});
