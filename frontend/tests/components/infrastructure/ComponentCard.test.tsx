import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ComponentCard } from "@/components/components/ComponentCard";
import type { ComponentState } from "@/lib/types";

const mockApiPost = jest.fn();
jest.mock("@/lib/api", () => ({
  api: { post: (...args: unknown[]) => mockApiPost(...args) },
  ApiError: class ApiError extends Error {},
}));

// Next.js Link
jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

const enabledComponent: ComponentState = {
  key: "k8s_pipeline_pool",
  name: "K8s Pipeline Pool",
  description: "Node pool for pipeline jobs",
  category: "compute",
  enabled: true,
  status: "healthy",
  config: {},
  dependencies: [],
  estimated_monthly_cost: "$50-200/month",
  updated_at: null,
};

const disabledComponent: ComponentState = {
  ...enabledComponent,
  key: "cellxgene",
  name: "Cellxgene",
  enabled: false,
  status: "not_enabled",
};

const componentWithDeps: ComponentState = {
  ...disabledComponent,
  key: "qc_dashboard",
  name: "QC Dashboard",
  dependencies: ["k8s_pipeline_pool"],
};

describe("ComponentCard", () => {
  beforeEach(() => {
    mockApiPost.mockReset();
    mockApiPost.mockResolvedValue({});
  });

  it("renders component name and description", () => {
    render(<ComponentCard component={enabledComponent} onAction={jest.fn()} />);
    expect(screen.getByText("K8s Pipeline Pool")).toBeInTheDocument();
    expect(screen.getByText("Node pool for pipeline jobs")).toBeInTheDocument();
  });

  it("shows Disable button for enabled component", () => {
    render(<ComponentCard component={enabledComponent} onAction={jest.fn()} />);
    expect(screen.getByRole("button", { name: "Disable" })).toBeInTheDocument();
  });

  it("shows Enable button for disabled component", () => {
    render(<ComponentCard component={disabledComponent} onAction={jest.fn()} />);
    expect(screen.getByRole("button", { name: "Enable" })).toBeInTheDocument();
  });

  it("shows Configure link pointing to /components/{key}", () => {
    render(<ComponentCard component={enabledComponent} onAction={jest.fn()} />);
    const link = screen.getByRole("link", { name: "Configure" });
    expect(link).toHaveAttribute("href", "/components/k8s_pipeline_pool");
  });

  it("clicking Enable opens confirmation dialog", () => {
    render(<ComponentCard component={disabledComponent} onAction={jest.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Enable" }));
    expect(screen.getByText(/Enable Cellxgene/i)).toBeInTheDocument();
    expect(screen.getByText(/provision Cellxgene infrastructure/i)).toBeInTheDocument();
  });

  it("clicking Disable opens confirmation dialog with danger message", () => {
    render(<ComponentCard component={enabledComponent} onAction={jest.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Disable" }));
    expect(screen.getByText(/Disable K8s Pipeline Pool/i)).toBeInTheDocument();
    expect(screen.getByText(/cannot be undone/i)).toBeInTheDocument();
  });

  it("confirming Enable calls enable endpoint and triggers onAction", async () => {
    const onAction = jest.fn();
    render(<ComponentCard component={disabledComponent} onAction={onAction} />);
    fireEvent.click(screen.getByRole("button", { name: "Enable" }));
    const enableButtons = screen.getAllByRole("button", { name: "Enable" });
    fireEvent.click(enableButtons[enableButtons.length - 1]);
    await waitFor(() => {
      expect(mockApiPost).toHaveBeenCalledWith("/api/components/cellxgene/enable");
      expect(onAction).toHaveBeenCalled();
    });
  });

  it("confirming Disable calls disable endpoint and triggers onAction", async () => {
    const onAction = jest.fn();
    render(<ComponentCard component={enabledComponent} onAction={onAction} />);
    fireEvent.click(screen.getByRole("button", { name: "Disable" }));
    const disableButtons = screen.getAllByRole("button", { name: "Disable" });
    fireEvent.click(disableButtons[disableButtons.length - 1]);
    await waitFor(() => {
      expect(mockApiPost).toHaveBeenCalledWith("/api/components/k8s_pipeline_pool/disable");
      expect(onAction).toHaveBeenCalled();
    });
  });

  it("shows dependency list when dependencies are present", () => {
    render(<ComponentCard component={componentWithDeps} onAction={jest.fn()} />);
    expect(screen.getByText(/Requires:/)).toBeInTheDocument();
    expect(screen.getByText(/k8s_pipeline_pool/)).toBeInTheDocument();
  });

  it("does not show Requires when dependencies are empty", () => {
    render(<ComponentCard component={enabledComponent} onAction={jest.fn()} />);
    expect(screen.queryByText(/Requires:/)).not.toBeInTheDocument();
  });

  it("shows Coming Soon card when comingSoon is true", () => {
    render(
      <ComponentCard
        component={disabledComponent}
        onAction={jest.fn()}
        comingSoon
        comingSoonMessage="Coming Soon — available with SLURM compute stack"
      />
    );
    expect(screen.getByText("Coming Soon")).toBeInTheDocument();
    expect(screen.getByText("Coming Soon — available with SLURM compute stack")).toBeInTheDocument();
    // No enable/disable buttons on coming soon cards
    expect(screen.queryByRole("button", { name: "Enable" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Disable" })).not.toBeInTheDocument();
  });

  it("shows error message on API failure", async () => {
    mockApiPost.mockRejectedValue(new Error("Terraform error"));
    render(<ComponentCard component={disabledComponent} onAction={jest.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Enable" }));
    const enableButtons = screen.getAllByRole("button", { name: "Enable" });
    fireEvent.click(enableButtons[enableButtons.length - 1]);
    await waitFor(() => {
      expect(screen.getByText("Action failed")).toBeInTheDocument();
    });
  });
});
