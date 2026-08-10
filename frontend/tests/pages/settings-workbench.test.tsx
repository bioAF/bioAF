import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import WorkbenchSettingsPage from "@/app/(app)/settings/work-nodes/page";

const mockPush = jest.fn();
jest.mock("next/navigation", () => ({
  usePathname: () => "/settings/work-nodes",
  useRouter: () => ({ push: mockPush }),
}));

jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ email: "admin@bioaf.org", role: "admin", sub: "1" }),
}));

jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, loading: false }),
}));

const mockApiGet = jest.fn();
const mockApiPut = jest.fn();
jest.mock("@/lib/api", () => {
  const actual = jest.requireActual("@/lib/api");
  return {
    api: {
      get: (...args: unknown[]) => mockApiGet(...args),
      put: (...args: unknown[]) => mockApiPut(...args),
    },
    ApiError: actual.ApiError,
    extractErrorMessage: actual.extractErrorMessage,
  };
});

const workNodeConfig = {
  max_nodes_per_user: 2,
  idle_timeout_hours: 24,
  boot_disk_gb: 150,
  boot_disk_type: "pd-standard",
};

const notebookConfig = {
  idle_timeout_hours: 4,
  idle_warning_minutes: 15,
  max_sessions_per_user: 2,
};

describe("Workbench Settings Page - work-node boot disk", () => {
  beforeEach(() => {
    mockApiGet.mockReset();
    mockApiPut.mockReset();
    mockApiGet.mockImplementation((url: string) => {
      if (url.includes("/api/v1/settings/work-nodes")) {
        return Promise.resolve(workNodeConfig);
      }
      if (url.includes("/api/v1/settings/notebooks")) {
        return Promise.resolve(notebookConfig);
      }
      return Promise.resolve({});
    });
  });

  it("loads the configured boot disk size and type", async () => {
    render(<WorkbenchSettingsPage />);
    await waitFor(() => {
      const gb = screen.getByLabelText("Boot Disk Size (GB)") as HTMLInputElement;
      const type = screen.getByLabelText("Boot Disk Type") as HTMLSelectElement;
      expect(gb.value).toBe("150");
      expect(type.value).toBe("pd-standard");
    });
  });

  it("offers the three supported disk types", async () => {
    render(<WorkbenchSettingsPage />);
    await waitFor(() => screen.getByLabelText("Boot Disk Type"));
    const type = screen.getByLabelText("Boot Disk Type") as HTMLSelectElement;
    const values = Array.from(type.options).map((o) => o.value);
    expect(values).toEqual(
      expect.arrayContaining(["pd-ssd", "pd-balanced", "pd-standard"]),
    );
  });

  it("PUTs the edited boot disk settings on save", async () => {
    mockApiPut.mockResolvedValueOnce({});
    render(<WorkbenchSettingsPage />);
    await waitFor(() => screen.getByLabelText("Boot Disk Size (GB)"));

    fireEvent.change(screen.getByLabelText("Boot Disk Size (GB)"), {
      target: { value: "100" },
    });
    fireEvent.change(screen.getByLabelText("Boot Disk Type"), {
      target: { value: "pd-ssd" },
    });
    // The Work Nodes section's Save button is the first of the two on the page.
    fireEvent.click(screen.getAllByRole("button", { name: /save/i })[0]);

    await waitFor(() => {
      expect(mockApiPut).toHaveBeenCalledWith(
        expect.stringContaining("/api/v1/settings/work-nodes"),
        expect.objectContaining({ boot_disk_gb: 100, boot_disk_type: "pd-ssd" }),
      );
    });
  });
});
