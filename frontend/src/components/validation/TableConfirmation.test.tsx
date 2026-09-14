/**
 * plan_8_2 section 3.1: when bioAF's own evidence does not establish how a table reads, a person may record
 * it, with the evidence it rests on. Column numbers are shown counting from 1 and sent counting from 0. The
 * labels are pending the owner's sign-off.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { TableConfirmation } from "./TableConfirmation";

let allowed = true;
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => allowed, roleName: "admin", loading: false }),
}));
jest.mock("@/lib/api", () => ({ api: { post: jest.fn() } }));

import { api } from "@/lib/api";

const mockPost = api.post as jest.Mock;

const PROPS = {
  studyId: 7,
  table: "GSE1_DeSeq2-Differentiation.txt.gz",
  contrast: "KO vs WT (day 7)",
  columnsCount: 7,
  candidateRoles: { id: [0], lfc: [1, 2, 3, 4, 5, 6], pvalue: [3, 5, 6], padj: [3, 5, 6] },
};

beforeEach(() => {
  allowed = true;
  mockPost.mockReset();
});

test("records how the table reads, with the evidence, and sends column numbers counting from 0", async () => {
  const onChanged = jest.fn();
  mockPost.mockResolvedValue({ id: 7, confirmation: {} });
  render(<TableConfirmation {...PROPS} onChanged={onChanged} />);
  fireEvent.click(screen.getByRole("button", { name: "Record how this table reads" }));

  expect(screen.getAllByText(/could be columns 4, 6, 7/).length).toBeGreaterThan(0);
  fireEvent.click(screen.getByLabelText(`This table reports ${PROPS.contrast}`));
  fireEvent.change(screen.getByLabelText("Identifier column"), { target: { value: "1" } });
  fireEvent.change(screen.getByLabelText("Fold change column"), { target: { value: "3" } });
  fireEvent.change(screen.getByLabelText("P value column"), { target: { value: "6" } });
  fireEvent.change(screen.getByLabelText("Adjusted P value column"), { target: { value: "7" } });
  fireEvent.change(screen.getByLabelText("Effect scale"), { target: { value: "log2" } });

  const save = screen.getByRole("button", { name: "Record" });
  expect(save).toBeDisabled();
  fireEvent.change(screen.getByLabelText("What establishes this"), {
    target: { value: "The series README lists the columns." },
  });
  fireEvent.click(save);

  await waitFor(() => expect(onChanged).toHaveBeenCalledWith({ id: 7, confirmation: {} }));
  expect(mockPost).toHaveBeenCalledWith("/api/validation-studies/7/table-confirmations", {
    table: PROPS.table,
    contrast: PROPS.contrast,
    reports_contrast: true,
    columns: { id: 0, lfc: 2, pvalue: 5, padj: 6 },
    effect_scale: "log2",
    orientation: null,
    note: "The series README lists the columns.",
  });
});

test("a refusal is shown in the server's words", async () => {
  mockPost.mockRejectedValueOnce(Object.assign(new Error("two roles name the same column"), { status: 422 }));
  render(<TableConfirmation {...PROPS} onChanged={jest.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Record how this table reads" }));
  fireEvent.click(screen.getByLabelText(`This table reports ${PROPS.contrast}`));
  fireEvent.change(screen.getByLabelText("What establishes this"), { target: { value: "A legend." } });
  fireEvent.click(screen.getByRole("button", { name: "Record" }));
  expect(await screen.findByText("two roles name the same column")).toBeInTheDocument();
});

test("a person who may only view the study sees no control", () => {
  allowed = false;
  const { container } = render(<TableConfirmation {...PROPS} onChanged={jest.fn()} />);
  expect(container).toBeEmptyDOMElement();
});
