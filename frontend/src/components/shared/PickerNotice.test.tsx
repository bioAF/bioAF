import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PickerNotice } from "./PickerNotice";

const props = { deniedMessage: "Your role does not include this.", failedWhat: "Projects" };

test("says nothing while loading or once loaded", () => {
  const { container, rerender } = render(<PickerNotice status="loading" {...props} />);
  expect(container).toBeEmptyDOMElement();
  rerender(<PickerNotice status="loaded" {...props} />);
  expect(container).toBeEmptyDOMElement();
});

test("explains a denial, with nothing to retry", () => {
  render(<PickerNotice status="denied" {...props} onRetry={jest.fn()} />);
  expect(screen.getByText(props.deniedMessage)).toBeInTheDocument();
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
});

test("reports a failure and offers a retry", async () => {
  const onRetry = jest.fn();
  render(<PickerNotice status="failed" {...props} onRetry={onRetry} />);
  expect(screen.getByText(/Projects could not be loaded/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(onRetry).toHaveBeenCalled();
});
