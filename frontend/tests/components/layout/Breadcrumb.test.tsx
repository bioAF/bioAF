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

  it("trails Lab Knowledge > Literature on the literature library", () => {
    mockPathname.mockReturnValue("/lab-knowledge/literature");
    render(<Breadcrumb />);
    const breadcrumb = screen.getByTestId("breadcrumb");
    expect(breadcrumb).toHaveTextContent("Lab Knowledge");
    expect(screen.getByTestId("breadcrumb-current")).toHaveTextContent("Literature");
  });

  it("trails Lab Knowledge > Literature > paper title on a paper detail page", () => {
    mockPathname.mockReturnValue("/lab-knowledge/literature/papers/123");
    render(<Breadcrumb entityName="A CRISPR screen paper" />);
    const breadcrumb = screen.getByTestId("breadcrumb");
    expect(breadcrumb).toHaveTextContent("Lab Knowledge");
    expect(breadcrumb).toHaveTextContent("Literature");
    expect(screen.getByTestId("breadcrumb-current")).toHaveTextContent("A CRISPR screen paper");
    // Literature is an intermediate crumb, so it links back to the library.
    const litLink = screen.getByRole("link", { name: "Literature" });
    expect(litLink).toHaveAttribute("href", "/lab-knowledge/literature");
  });

  it("trails Lab Knowledge > Validation Studies on the validation list", () => {
    mockPathname.mockReturnValue("/lab-knowledge/validation-studies");
    render(<Breadcrumb />);
    expect(screen.getByTestId("breadcrumb-current")).toHaveTextContent("Validation Studies");
  });

  it("trails Lab Knowledge > Validation Studies > study on a validation detail page", () => {
    mockPathname.mockReturnValue("/lab-knowledge/validation-studies/5");
    render(<Breadcrumb entityName="Study #5" />);
    const breadcrumb = screen.getByTestId("breadcrumb");
    expect(breadcrumb).toHaveTextContent("Validation Studies");
    expect(screen.getByTestId("breadcrumb-current")).toHaveTextContent("Study #5");
    const listLink = screen.getByRole("link", { name: "Validation Studies" });
    expect(listLink).toHaveAttribute("href", "/lab-knowledge/validation-studies");
  });
});
