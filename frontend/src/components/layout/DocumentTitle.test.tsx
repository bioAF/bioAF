import { render } from "@testing-library/react";
import { DocumentTitle } from "./DocumentTitle";

const mockPathname = jest.fn(() => "/pipelines/runs");
jest.mock("next/navigation", () => ({
  usePathname: () => mockPathname(),
}));

beforeEach(() => {
  document.title = "bioAF";
  mockPathname.mockReturnValue("/pipelines/runs");
});

test("names the tab after the page", () => {
  render(<DocumentTitle />);
  expect(document.title).toBe("Pipeline Runs - bioAF");
});

test("renames the tab when the route changes under a client-side nav", () => {
  const { rerender } = render(<DocumentTitle />);
  expect(document.title).toBe("Pipeline Runs - bioAF");

  mockPathname.mockReturnValue("/settings/users");
  rerender(<DocumentTitle />);
  expect(document.title).toBe("Users & Accounts - bioAF");
});

test("renders nothing", () => {
  const { container } = render(<DocumentTitle />);
  expect(container).toBeEmptyDOMElement();
});
