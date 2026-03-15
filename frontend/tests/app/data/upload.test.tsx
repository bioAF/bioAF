import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import DataUploadPage from "@/app/data/upload/page";

jest.mock("@/components/layout/Sidebar", () => ({
  Sidebar: () => <div data-testid="sidebar" />,
}));
jest.mock("@/components/layout/Header", () => ({
  Header: () => <div data-testid="header" />,
}));
jest.mock("@/lib/auth", () => ({
  getToken: () => "test-token",
  removeToken: jest.fn(),
}));

const mockUploadSigned = jest.fn();
jest.mock("@/lib/api", () => ({
  api: {
    uploadSigned: (...args: unknown[]) => mockUploadSigned(...args),
  },
}));

beforeEach(() => {
  mockUploadSigned.mockReset();
  mockUploadSigned.mockResolvedValue({ id: 1, filename: "sample.fastq.gz" });
});

describe("DataUploadPage", () => {
  it("calls uploadSigned when uploading a file", async () => {
    render(<DataUploadPage />);

    const file = new File(["data"], "sample.fastq.gz", { type: "application/gzip" });
    const input = document.querySelector("input[type='file']") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => screen.getByText(/Upload 1 file/));
    fireEvent.click(screen.getByText(/Upload 1 file/));

    await waitFor(() => expect(mockUploadSigned).toHaveBeenCalledTimes(1));
    const [calledFile, calledOptions] = mockUploadSigned.mock.calls[0];
    expect(calledFile).toBe(file);
    expect(calledOptions.experimentId).toBeUndefined();
  });

  it("passes experimentId option when experiment ID is provided", async () => {
    render(<DataUploadPage />);

    fireEvent.change(screen.getByPlaceholderText("Experiment ID"), {
      target: { value: "42" },
    });

    const file = new File(["data"], "sample.fastq.gz", { type: "application/gzip" });
    const input = document.querySelector("input[type='file']") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => screen.getByText(/Upload 1 file/));
    fireEvent.click(screen.getByText(/Upload 1 file/));

    await waitFor(() => expect(mockUploadSigned).toHaveBeenCalledTimes(1));
    const [, calledOptions] = mockUploadSigned.mock.calls[0];
    expect(calledOptions.experimentId).toBe(42);
  });
});
