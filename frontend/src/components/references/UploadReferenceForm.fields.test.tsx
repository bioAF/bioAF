/**
 * The upload form's fields, constraints and feedback. Native validation is
 * the browser's (required, read-only, url type); the form's own check covers
 * what the browser cannot (no files chosen) and every failure is announced.
 */
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
  mockGet.mockReset().mockResolvedValue({ references: [], total: 0 });
  mockPost.mockReset();
  mockUpload.mockReset();
});

function form() {
  return document.querySelector("form") as HTMLFormElement;
}

test("every field has a visible label and keeps its native constraints", () => {
  render(<UploadReferenceForm onCreated={jest.fn()} onCancel={jest.fn()} />);
  expect(screen.getByLabelText("Name *")).toBeRequired();
  expect(screen.getByLabelText("Version")).toHaveAccessibleDescription("Filled in automatically; override it to keep your own scheme.");
  expect(screen.getByLabelText("Category *")).toBeRequired();
  expect(screen.getByLabelText("Scope *")).toBeRequired();
  expect(screen.getByLabelText("Source URL (optional)")).toHaveAttribute("type", "url");
  expect(screen.getByLabelText("Description (optional)").tagName).toBe("TEXTAREA");
  expect(screen.getByLabelText("Files *")).toHaveAttribute("type", "file");
  expect(screen.getByLabelText("Files *")).toHaveAttribute("multiple");
});

test("locked name is read-only and locked category is disabled", () => {
  render(
    <UploadReferenceForm lockedName="GRCh38" lockedCategory="annotation" onCreated={jest.fn()} onCancel={jest.fn()} />,
  );
  expect(screen.getByLabelText("Name *")).toHaveAttribute("readonly");
  expect(screen.getByLabelText("Name *")).toHaveValue("GRCh38");
  expect(screen.getByLabelText("Category *")).toBeDisabled();
  expect(screen.getByLabelText("Category *")).toHaveValue("annotation");
  expect(screen.getByLabelText("Scope *")).not.toBeDisabled();
});

test("submitting without files is refused with an announced error", async () => {
  render(<UploadReferenceForm onCreated={jest.fn()} onCancel={jest.fn()} />);
  fireEvent.change(screen.getByLabelText("Name *"), { target: { value: "Mouse" } });
  fireEvent.change(screen.getByLabelText("Version"), { target: { value: "v1" } });
  fireEvent.submit(form());
  expect(await screen.findByRole("alert")).toHaveTextContent("Fill in all required fields and select at least one file.");
  expect(mockPost).not.toHaveBeenCalled();
});

test("an upload failure is announced, the reference is aborted and the form can retry", async () => {
  mockPost.mockImplementation((url: string) =>
    url === "/api/references/upload-init"
      ? Promise.resolve({ reference_id: 5, uploads: [{ filename: "a.fa", session_url: "s" }] })
      : Promise.resolve({}),
  );
  mockUpload.mockRejectedValueOnce(new Error("The upload was interrupted."));
  render(<UploadReferenceForm onCreated={jest.fn()} onCancel={jest.fn()} />);
  fireEvent.change(screen.getByLabelText("Name *"), { target: { value: "Mouse" } });
  fireEvent.change(screen.getByLabelText("Version"), { target: { value: "v1" } });
  await userEvent.upload(screen.getByLabelText("Files *"), new File(["ACGT"], "a.fa"));
  expect(screen.getByText("a.fa")).toBeInTheDocument();
  fireEvent.submit(form());
  expect(await screen.findByRole("alert")).toHaveTextContent("The upload was interrupted.");
  await waitFor(() => expect(mockPost).toHaveBeenCalledWith("/api/references/5/abort"));
  expect(screen.getByRole("button", { name: "Start upload" })).not.toBeDisabled();
});

test("Cancel never submits and is held while an upload runs", async () => {
  const onCancel = jest.fn();
  let finishInit: (v: unknown) => void = () => {};
  mockPost.mockImplementation(() => new Promise((r) => (finishInit = r)));
  render(<UploadReferenceForm onCreated={jest.fn()} onCancel={onCancel} />);
  const cancel = screen.getByRole("button", { name: "Cancel" });
  expect(cancel).toHaveAttribute("type", "button");
  await userEvent.click(cancel);
  expect(onCancel).toHaveBeenCalledTimes(1);
  expect(mockPost).not.toHaveBeenCalled();

  fireEvent.change(screen.getByLabelText("Name *"), { target: { value: "Mouse" } });
  fireEvent.change(screen.getByLabelText("Version"), { target: { value: "v1" } });
  await userEvent.upload(screen.getByLabelText("Files *"), new File(["ACGT"], "a.fa"));
  fireEvent.submit(form());
  await waitFor(() => expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled());
  expect(screen.getByRole("button", { name: "Uploading..." })).toBeDisabled();
  expect(screen.getByLabelText("Files *")).toBeDisabled();
  finishInit({ reference_id: 1, uploads: [] });
});
