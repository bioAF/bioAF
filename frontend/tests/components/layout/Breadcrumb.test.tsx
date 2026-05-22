import { render, screen } from "@testing-library/react";
import { Breadcrumb } from "@/components/layout/Breadcrumb";

const mockPathname = jest.fn().mockReturnValue("/dashboard");
jest.mock("next/navigation", () => ({
  usePathname: () => mockPathname(),
}));

describe("Breadcrumb", () => {
  it("renders correct segment for a top-level page", () => {
    mockPathname.mockReturnValue("/dashboard");
    render(<Breadcrumb />);
    const breadcrumb = screen.getByTestId("breadcrumb");
    expect(breadcrumb).toHaveTextContent("Dashboard");
    // Should be plain text (last segment)
    const current = screen.getByTestId("breadcrumb-current");
    expect(current).toHaveTextContent("Dashboard");
  });

  it("renders correct segments for a child page", () => {
    mockPathname.mockReturnValue("/pipelines/catalog");
    render(<Breadcrumb />);
    const breadcrumb = screen.getByTestId("breadcrumb");
    expect(breadcrumb).toHaveTextContent("Pipelines");
    expect(breadcrumb).toHaveTextContent("Pipeline Catalog");
  });

  it("renders correct segments for a detail page with entity name", () => {
    mockPathname.mockReturnValue("/projects/experiments");
    render(<Breadcrumb entityName="Experiment 123" />);
    const breadcrumb = screen.getByTestId("breadcrumb");
    // The top-level section was renamed from "Projects" to "Experiments".
    expect(breadcrumb).toHaveTextContent("Experiments");
    expect(breadcrumb).toHaveTextContent("Experiment List");
    expect(breadcrumb).toHaveTextContent("Experiment 123");
  });

  it("makes intermediate segments clickable links", () => {
    mockPathname.mockReturnValue("/pipelines/catalog");
    render(<Breadcrumb />);
    // "Pipelines" should be a link (intermediate segment)
    const links = screen.getAllByRole("link");
    expect(links.length).toBeGreaterThan(0);
    expect(links[0]).toHaveTextContent("Pipelines");
  });

  it("renders last segment as plain text", () => {
    mockPathname.mockReturnValue("/pipelines/catalog");
    render(<Breadcrumb />);
    const current = screen.getByTestId("breadcrumb-current");
    expect(current).toHaveTextContent("Pipeline Catalog");
    expect(current.tagName).toBe("SPAN");
  });

  it("shows Experiments as top breadcrumb segment when on experiment detail page", () => {
    mockPathname.mockReturnValue("/projects/experiments/42");
    render(<Breadcrumb entityName="My Experiment" />);
    const breadcrumb = screen.getByTestId("breadcrumb");
    expect(breadcrumb).toHaveTextContent("Experiments");
    expect(breadcrumb).toHaveTextContent("Experiment List");
    expect(breadcrumb).toHaveTextContent("My Experiment");
  });
});
