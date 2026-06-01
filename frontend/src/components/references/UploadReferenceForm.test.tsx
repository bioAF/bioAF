import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { UploadReferenceForm } from "./UploadReferenceForm";

const mockGet = jest.fn();
const mockPost = jest.fn();
jest.mock("@/lib/api", () => ({
  api: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
  },
}));

const mockUpload = jest.fn();
jest.mock("@/lib/resumableUpload", () => ({
  uploadFileResumable: (...args: unknown[]) => mockUpload(...args),
}));

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
  mockUpload.mockReset();
});

describe("UploadReferenceForm - auto-version prefill", () => {
  it("Pre-fills version='v1' on mount when name+category are locked and no prior versions exist", async () => {
    mockGet.mockResolvedValue({ references: [], total: 0 });

    render(
      <UploadReferenceForm
        lockedName="GRCh38 GENCODE"
        lockedCategory="genome"
        lockedScope="public"
        onCreated={jest.fn()}
        onCancel={jest.fn()}
      />,
    );

    await waitFor(() => {
      expect(mockGet).toHaveBeenCalledWith(
        expect.stringMatching(/^\/api\/references\/by-name\?.*name=GRCh38\+GENCODE.*category=genome/),
      );
    });
    const versionInput = screen.getByLabelText(/version/i) as HTMLInputElement;
    await waitFor(() => expect(versionInput.value).toBe("v1"));
  });

  it("Pre-fills the next 'v<max+1>' when prior versions exist", async () => {
    mockGet.mockResolvedValue({
      references: [{ version: "v1" }, { version: "v3" }, { version: "v2" }],
      total: 3,
    });

    render(
      <UploadReferenceForm
        lockedName="GRCh38 GENCODE"
        lockedCategory="genome"
        lockedScope="public"
        onCreated={jest.fn()}
        onCancel={jest.fn()}
      />,
    );

    const versionInput = screen.getByLabelText(/version/i) as HTMLInputElement;
    await waitFor(() => expect(versionInput.value).toBe("v4"));
  });

  it("Predicts the version after the user types a name that matches an existing reference", async () => {
    mockGet.mockResolvedValue({
      references: [{ version: "v1" }, { version: "v2" }],
      total: 2,
    });

    render(<UploadReferenceForm onCreated={jest.fn()} onCancel={jest.fn()} />);

    const nameInput = screen.getByLabelText(/name/i) as HTMLInputElement;
    fireEvent.change(nameInput, { target: { value: "Mouse mm10" } });
    fireEvent.blur(nameInput);

    const versionInput = screen.getByLabelText(/version/i) as HTMLInputElement;
    await waitFor(() => expect(versionInput.value).toBe("v3"));
  });

  it("Does not overwrite a version the user has typed manually", async () => {
    mockGet.mockResolvedValue({
      references: [{ version: "v1" }, { version: "v2" }],
      total: 2,
    });

    render(<UploadReferenceForm onCreated={jest.fn()} onCancel={jest.fn()} />);

    const versionInput = screen.getByLabelText(/version/i) as HTMLInputElement;
    fireEvent.change(versionInput, { target: { value: "v4-custom" } });

    const nameInput = screen.getByLabelText(/name/i) as HTMLInputElement;
    fireEvent.change(nameInput, { target: { value: "Mouse mm10" } });
    fireEvent.blur(nameInput);

    // Wait long enough for any prefill to have run.
    await new Promise((r) => setTimeout(r, 10));
    expect(versionInput.value).toBe("v4-custom");
  });
});
