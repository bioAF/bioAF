import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { UrlImportReferenceForm } from "./UrlImportReferenceForm";

const mockGet = jest.fn();
const mockPost = jest.fn();
jest.mock("@/lib/api", () => ({
  api: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
  },
}));

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
});

describe("UrlImportReferenceForm - auto-extract from URL", () => {
  it("Selects extract='tar.gz' when the URL ends in .tar.gz", () => {
    render(<UrlImportReferenceForm onStarted={jest.fn()} onCancel={jest.fn()} />);
    const urlInput = screen.getByLabelText(/source url/i) as HTMLInputElement;
    fireEvent.change(urlInput, {
      target: { value: "https://cf.10xgenomics.com/supp/cell-exp/refdata.tar.gz" },
    });
    const extractSelect = screen.getByLabelText(/extract/i) as HTMLSelectElement;
    expect(extractSelect.value).toBe("tar.gz");
  });

  it("Selects extract='gzip' when the URL ends in .gz", () => {
    render(<UrlImportReferenceForm onStarted={jest.fn()} onCancel={jest.fn()} />);
    fireEvent.change(screen.getByLabelText(/source url/i), {
      target: { value: "https://ftp.example.com/gencode.v45.annotation.gtf.gz" },
    });
    expect((screen.getByLabelText(/extract/i) as HTMLSelectElement).value).toBe("gzip");
  });

  it("Does not override the user's explicit extract choice once they pick one", () => {
    render(<UrlImportReferenceForm onStarted={jest.fn()} onCancel={jest.fn()} />);
    fireEvent.change(screen.getByLabelText(/source url/i), {
      target: { value: "https://ftp.example.com/file.gz" },
    });
    // User overrides to 'none' (they want the raw .gz stored).
    fireEvent.change(screen.getByLabelText(/extract/i), { target: { value: "none" } });
    // Then tweaks the URL again to a .tar.gz: the auto-detect should NOT
    // stomp the user's explicit selection.
    fireEvent.change(screen.getByLabelText(/source url/i), {
      target: { value: "https://ftp.example.com/file.tar.gz" },
    });
    expect((screen.getByLabelText(/extract/i) as HTMLSelectElement).value).toBe("none");
  });
});

describe("UrlImportReferenceForm - auto-version prefill", () => {
  it("Predicts the next version after the user types a name", async () => {
    mockGet.mockResolvedValue({
      references: [{ version: "v1" }, { version: "v2" }],
      total: 2,
    });
    render(<UrlImportReferenceForm onStarted={jest.fn()} onCancel={jest.fn()} />);
    const nameInput = screen.getByLabelText(/name/i) as HTMLInputElement;
    fireEvent.change(nameInput, { target: { value: "10x Human GEX" } });
    fireEvent.blur(nameInput);
    const versionInput = screen.getByLabelText(/version/i) as HTMLInputElement;
    await waitFor(() => expect(versionInput.value).toBe("v3"));
  });
});
