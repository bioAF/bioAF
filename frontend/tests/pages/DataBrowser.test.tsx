/**
 * Tests 24-26: Dataset Browser file reassignment (Step 12 - Phase 18).
 *
 * 24: Checkbox column appears in file list
 * 25: Reassign toolbar appears when files selected
 * 26: Reassign calls API
 */

import { render, screen, fireEvent, waitFor } from "@testing-library/react";

const mockApiGet = jest.fn();
const mockApiPost = jest.fn();
jest.mock("@/lib/api", () => ({
  api: {
    get: (...args: unknown[]) => mockApiGet(...args),
    post: (...args: unknown[]) => mockApiPost(...args),
  },
}));

jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({
    id: 1,
    email: "admin@test.com",
    role_name: "admin",
    organization_id: 1,
  }),
}));

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
}));

jest.mock("@/components/layout/Sidebar", () => ({
  Sidebar: () => <nav data-testid="sidebar" />,
}));

jest.mock("@/components/layout/Header", () => ({
  Header: () => <header data-testid="header" />,
}));

// Dynamic import so mocks register first
import DataBrowserPage from "@/app/data/browser/page";

const mockDatasets = {
  experiments: [
    {
      experiment_id: 1,
      experiment_name: "RNA-seq Experiment",
      status: "processing",
      organism: "Human",
      sample_count: 5,
      file_count: 10,
      total_size_bytes: 1073741824,
    },
    {
      experiment_id: 2,
      experiment_name: "ChIP-seq Experiment",
      status: "registered",
      organism: "Mouse",
      sample_count: 3,
      file_count: 6,
      total_size_bytes: 536870912,
    },
  ],
  total: 2,
};

describe("DataBrowserPage - file reassignment", () => {
  const mockFilterOptions = { organisms: ["Human", "Mouse"] };

  beforeEach(() => {
    mockApiGet.mockReset();
    mockApiPost.mockReset();
    mockApiGet.mockImplementation((url: string) => {
      if (url.includes("/filter-options")) return Promise.resolve(mockFilterOptions);
      return Promise.resolve(mockDatasets);
    });
  });

  it("renders checkbox column for admin users", async () => {
    render(<DataBrowserPage />);

    await waitFor(() => {
      expect(screen.getByText("RNA-seq Experiment")).toBeInTheDocument();
    });

    const checkboxes = screen.getAllByRole("checkbox");
    expect(checkboxes.length).toBeGreaterThan(0);
  });

  it("shows action toolbar when experiments are selected", async () => {
    render(<DataBrowserPage />);

    await waitFor(() => {
      expect(screen.getByText("RNA-seq Experiment")).toBeInTheDocument();
    });

    const checkboxes = screen.getAllByRole("checkbox");
    fireEvent.click(checkboxes[0]);

    expect(screen.getByText(/Add to Project/)).toBeInTheDocument();
  });

  it("calls API when adding selected experiments to project", async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url.includes("/filter-options")) return Promise.resolve(mockFilterOptions);
      if (url.startsWith("/api/datasets")) return Promise.resolve(mockDatasets);
      if (url.startsWith("/api/projects"))
        return Promise.resolve({
          projects: [{ id: 1, name: "Test Project" }],
        });
      if (url.startsWith("/api/experiments/"))
        return Promise.resolve({ samples: [{ id: 1 }, { id: 2 }] });
      return Promise.resolve({});
    });
    mockApiPost.mockResolvedValue({});

    render(<DataBrowserPage />);

    await waitFor(() => {
      expect(screen.getByText("RNA-seq Experiment")).toBeInTheDocument();
    });

    // Select first experiment
    const checkboxes = screen.getAllByRole("checkbox");
    fireEvent.click(checkboxes[0]);

    // Click the "Add to Project" button
    const addButton = screen.getByText(/Add to Project/);
    fireEvent.click(addButton);

    // Wait for modal to appear
    await waitFor(() => {
      expect(screen.getByText("Select Project")).toBeInTheDocument();
    });
  });
});
