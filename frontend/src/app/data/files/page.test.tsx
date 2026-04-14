import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import DataFilesPage from "./page";

jest.mock("next/link", () => {
  return function MockLink({
    children,
    href,
  }: {
    children: React.ReactNode;
    href: string;
  }) {
    return <a href={href}>{children}</a>;
  };
});

jest.mock("@/components/layout/Sidebar", () => ({
  Sidebar: () => <nav data-testid="sidebar" />,
}));
jest.mock("@/components/layout/Header", () => ({
  Header: () => <header data-testid="header" />,
}));

jest.mock("@/lib/api", () => ({
  api: {
    get: jest.fn(),
    post: jest.fn(),
    delete: jest.fn(),
  },
  fileContentUrl: (fileId: number) =>
    Promise.resolve(`http://localhost:8000/api/files/${fileId}/content?token=fake`),
}));

jest.mock("@/hooks/useContentUrl", () => ({
  useFileContentUrl: (id: number | null) =>
    id != null ? `http://localhost:8000/api/files/${id}/content?token=fake` : null,
}));

jest.mock("@/lib/auth", () => ({
  getToken: () => "fake-token",
  removeToken: jest.fn(),
  getCurrentUser: () => ({ role_name: "admin", sub: "1", org_id: "1" }),
}));

jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({
    canAccess: () => true,
    roleName: "admin",
    loading: false,
    permissions: new Set(["files:download", "files:delete", "files:upload"]),
  }),
}));

import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;
const mockPost = api.post as jest.Mock;
const mockDelete = api.delete as jest.Mock;

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
  mockDelete.mockReset();
});

const makeFile = (overrides: Record<string, unknown> = {}) => ({
  id: 1,
  filename: "sample_R1.fastq.gz",
  gcs_uri: "gs://bucket/sample_R1.fastq.gz",
  size_bytes: 1048576,
  md5_checksum: "abc123",
  file_type: "fastq",
  tags: [],
  uploader: { id: 1, name: "Maria", email: "maria@test.com" },
  project_id: null,
  experiment_id: null,
  sample_ids: [],
  upload_timestamp: "2026-03-01T00:00:00Z",
  created_at: "2026-03-01T00:00:00Z",
  ...overrides,
});

const filesResponse = {
  files: [
    makeFile(),
    makeFile({
      id: 2,
      filename: "counts.h5ad",
      gcs_uri: "gs://bucket/counts.h5ad",
      size_bytes: 5242880,
      md5_checksum: "def456",
      file_type: "h5ad",
      experiment_id: 10,
      upload_timestamp: "2026-03-02T00:00:00Z",
      created_at: "2026-03-02T00:00:00Z",
    }),
  ],
  total: 2,
  page: 1,
  page_size: 25,
};

const experimentsResponse = {
  experiments: [
    { id: 10, name: "RNA-seq Batch 1" },
    { id: 20, name: "ATAC-seq Pilot" },
  ],
  total: 2,
  page: 1,
  page_size: 100,
};

const projectsResponse = {
  projects: [{ id: 1, name: "Alpha Project", status: "active" }],
  total: 1,
};

function makeGetMock(filesData = filesResponse) {
  return (path: string) => {
    if (path.startsWith("/api/files")) return Promise.resolve(filesData);
    if (path.startsWith("/api/projects")) return Promise.resolve(projectsResponse);
    if (path.startsWith("/api/experiments")) return Promise.resolve(experimentsResponse);
    return Promise.resolve([]);
  };
}

test("renders file list from paginated response", async () => {
  mockGet.mockImplementation(makeGetMock());

  render(<DataFilesPage />);

  await waitFor(() => {
    expect(screen.getByText("sample_R1.fastq.gz")).toBeInTheDocument();
    expect(screen.getByText("counts.h5ad")).toBeInTheDocument();
  });
});

test("shows experiment name when file is linked", async () => {
  mockGet.mockImplementation(makeGetMock());

  render(<DataFilesPage />);

  await waitFor(() => {
    expect(screen.getByText("RNA-seq Batch 1")).toBeInTheDocument();
  });
});

test("shows unlinked label for files without experiment", async () => {
  mockGet.mockImplementation(makeGetMock());

  render(<DataFilesPage />);

  await waitFor(() => {
    expect(screen.getByText("Unlinked")).toBeInTheDocument();
  });
});

test("shows empty state when no files", async () => {
  mockGet.mockImplementation(makeGetMock({ files: [], total: 0, page: 1, page_size: 25 }));

  render(<DataFilesPage />);

  await waitFor(() => {
    expect(screen.getByText("No files found.")).toBeInTheDocument();
  });
});

test("formats file size in human-readable form", async () => {
  mockGet.mockImplementation(makeGetMock());

  render(<DataFilesPage />);

  await waitFor(() => {
    expect(screen.getByText("1.0 MB")).toBeInTheDocument();
    expect(screen.getByText("5.0 MB")).toBeInTheDocument();
  });
});

test("links single file to experiment via row Associate button", async () => {
  mockGet.mockImplementation(makeGetMock());
  mockPost.mockResolvedValue({ status: "linked" });

  render(<DataFilesPage />);

  await waitFor(() => {
    expect(screen.getByText("sample_R1.fastq.gz")).toBeInTheDocument();
  });

  // Click the "Associate" button on the unlinked file
  const associateButtons = screen.getAllByText("Associate");
  fireEvent.click(associateButtons[0]);

  // Modal should appear
  await waitFor(() => {
    expect(screen.getByText("Associate File")).toBeInTheDocument();
  });

  // Select an experiment from the modal's experiment dropdown
  const experimentSelect = screen.getByRole("combobox", { name: /experiment/i });
  fireEvent.change(experimentSelect, { target: { value: "20" } });

  fireEvent.click(screen.getByText("Save"));

  await waitFor(() => {
    expect(mockPost).toHaveBeenCalledWith("/api/files/1/link", {
      experiment_id: 20,
    });
  });
});

test("bulk-associates selected files to experiment", async () => {
  const threeUnlinked = {
    files: [
      makeFile({ id: 1, filename: "a.fastq.gz" }),
      makeFile({ id: 2, filename: "b.fastq.gz" }),
      makeFile({ id: 3, filename: "c.fastq.gz" }),
    ],
    total: 3,
    page: 1,
    page_size: 25,
  };

  mockGet.mockImplementation(makeGetMock(threeUnlinked));
  mockPost.mockResolvedValue({ status: "linked" });

  render(<DataFilesPage />);

  await waitFor(() => {
    expect(screen.getByText("a.fastq.gz")).toBeInTheDocument();
  });

  // Select files 1 and 3 via checkboxes
  const checkboxes = screen.getAllByRole("checkbox");
  // checkboxes[0] is select-all, [1]-[3] are rows
  fireEvent.click(checkboxes[1]);
  fireEvent.click(checkboxes[3]);

  // Bulk action bar should appear
  expect(screen.getByText("2 selected")).toBeInTheDocument();

  // Click the bulk "Associate" button (first one in DOM = toolbar button)
  fireEvent.click(screen.getAllByText("Associate")[0]);

  // Modal should appear
  await waitFor(() => {
    expect(screen.getByText("Associate 2 Files")).toBeInTheDocument();
  });

  // Select an experiment and save
  const experimentSelect = screen.getByRole("combobox", { name: /experiment/i });
  fireEvent.change(experimentSelect, { target: { value: "10" } });
  fireEvent.click(screen.getByText("Save"));

  await waitFor(() => {
    expect(mockPost).toHaveBeenCalledTimes(2);
    expect(mockPost).toHaveBeenCalledWith("/api/files/1/link", {
      experiment_id: 10,
    });
    expect(mockPost).toHaveBeenCalledWith("/api/files/3/link", {
      experiment_id: 10,
    });
  });
});

test("shows reconcile banner for stuck files and fixes on click", async () => {
  const stuckFiles = {
    files: [
      makeFile({
        id: 1,
        filename: "stuck.fastq.gz",
        gcs_uri: "gs://ingest-bucket/uploads/abc/stuck.fastq.gz",
        experiment_id: 10,
      }),
      makeFile({
        id: 2,
        filename: "ok.fastq.gz",
        gcs_uri: "gs://raw-bucket/experiments/10/ok.fastq.gz",
        experiment_id: 10,
      }),
    ],
    total: 2,
    page: 1,
    page_size: 25,
  };

  mockGet.mockImplementation(makeGetMock(stuckFiles));
  mockPost.mockResolvedValue({ reconciled: 1, failed: 0, skipped: 1 });

  render(<DataFilesPage />);

  // Banner should appear for the 1 stuck file
  await waitFor(() => {
    expect(
      screen.getByText("1 file needs to be synced to storage")
    ).toBeInTheDocument();
  });

  fireEvent.click(screen.getByText("Fix Now"));

  await waitFor(() => {
    expect(mockPost).toHaveBeenCalledWith("/api/files/reconcile");
  });

  // After reconcile, success message should appear
  await waitFor(() => {
    expect(
      screen.getByText("Done! 1 file synced to storage.")
    ).toBeInTheDocument();
  });
});

test("select-all checkbox toggles all rows", async () => {
  mockGet.mockImplementation(makeGetMock());

  render(<DataFilesPage />);

  await waitFor(() => {
    expect(screen.getByText("sample_R1.fastq.gz")).toBeInTheDocument();
  });

  const checkboxes = screen.getAllByRole("checkbox");
  const selectAll = checkboxes[0];

  // Check all
  fireEvent.click(selectAll);
  expect(screen.getByText("2 selected")).toBeInTheDocument();

  // Uncheck all
  fireEvent.click(selectAll);
  expect(screen.queryByText("2 selected")).not.toBeInTheDocument();
});

test("delete button removes selected files after confirmation", async () => {
  mockGet.mockImplementation(makeGetMock());
  mockDelete.mockResolvedValue({});

  render(<DataFilesPage />);

  await waitFor(() => {
    expect(screen.getByText("sample_R1.fastq.gz")).toBeInTheDocument();
  });

  // Select the first file
  const checkboxes = screen.getAllByRole("checkbox");
  fireEvent.click(checkboxes[1]);

  expect(screen.getByText("1 selected")).toBeInTheDocument();

  // Confirm the deletion
  window.confirm = jest.fn(() => true);
  fireEvent.click(screen.getByText("Delete"));

  await waitFor(() => {
    expect(mockDelete).toHaveBeenCalledWith("/api/files/1");
  });
});

test("delete button does nothing when user cancels confirmation", async () => {
  mockGet.mockImplementation(makeGetMock());

  render(<DataFilesPage />);

  await waitFor(() => {
    expect(screen.getByText("sample_R1.fastq.gz")).toBeInTheDocument();
  });

  const checkboxes = screen.getAllByRole("checkbox");
  fireEvent.click(checkboxes[1]);

  window.confirm = jest.fn(() => false);
  fireEvent.click(screen.getByText("Delete"));

  expect(mockDelete).not.toHaveBeenCalled();
});
