import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DepositConflictNotice } from "./DepositConflictNotice";

let canAccessResult = true;
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => canAccessResult, roleName: "admin", loading: false, permissions: new Set() }),
}));
jest.mock("@/lib/api", () => ({ api: { post: jest.fn() } }));
import { api } from "@/lib/api";
const mockPost = api.post as jest.Mock;

const CONFLICT = {
  message:
    "nf-core/atacseq does not consume Bisulfite-Seq data, and the accession this study was scoped to is deposited as Bisulfite-Seq.",
  suggested_pipeline_key: "nf-core/methylseq",
  library_strategy: "Bisulfite-Seq",
};

beforeEach(() => {
  canAccessResult = true;
  mockPost.mockReset();
  mockPost.mockResolvedValue({ id: 7, state: "plan_ready" });
});

test("says the tool MIGHT be wrong, not that it would be", () => {
  // The deposit is strong evidence, not proof: depositors mislabel series.
  render(<DepositConflictNotice studyId={7} conflict={CONFLICT} onChanged={jest.fn()} />);
  expect(screen.getByText(/might run the wrong tool/i)).toBeInTheDocument();
  expect(screen.queryByText(/would run the wrong tool/i)).not.toBeInTheDocument();
});

test("names both pipelines and what the record says the data is", () => {
  render(<DepositConflictNotice studyId={7} conflict={CONFLICT} onChanged={jest.fn()} />);
  expect(screen.getByText(/nf-core\/atacseq/)).toBeInTheDocument();
  expect(screen.getByText(/Bisulfite-Seq/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /use nf-core\/methylseq instead/i })).toBeInTheDocument();
});

test("the correction is one click and needs no explanation", async () => {
  const onChanged = jest.fn();
  render(<DepositConflictNotice studyId={7} conflict={CONFLICT} onChanged={onChanged} />);

  await userEvent.click(screen.getByRole("button", { name: /use nf-core\/methylseq instead/i }));

  await waitFor(() => expect(mockPost).toHaveBeenCalledWith("/api/validation-studies/7/use-deposit-pipeline", undefined));
  expect(onChanged).toHaveBeenCalled();
});

test("running it anyway asks why before it will send anything", async () => {
  render(<DepositConflictNotice studyId={7} conflict={CONFLICT} onChanged={jest.fn()} />);

  await userEvent.click(screen.getByRole("button", { name: /run it anyway/i }));
  const confirm = screen.getByRole("button", { name: /record.*run/i });
  expect(confirm).toBeDisabled();
  expect(mockPost).not.toHaveBeenCalled();

  await userEvent.type(screen.getByLabelText(/why/i), "the depositor labelled this series wrong");
  await userEvent.click(confirm);

  await waitFor(() =>
    expect(mockPost).toHaveBeenCalledWith("/api/validation-studies/7/override-deposit", {
      reason: "the depositor labelled this series wrong",
    }),
  );
});

test("offers only the override when the record names no replacement", () => {
  render(
    <DepositConflictNotice
      studyId={7}
      conflict={{ ...CONFLICT, suggested_pipeline_key: null }}
      onChanged={jest.fn()}
    />,
  );
  expect(screen.queryByRole("button", { name: /use nf-core/i })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: /run it anyway/i })).toBeInTheDocument();
});

test("shows a viewer why the study is held without offering them the controls", () => {
  canAccessResult = false;
  render(<DepositConflictNotice studyId={7} conflict={CONFLICT} onChanged={jest.fn()} />);
  expect(screen.getByText(/might run the wrong tool/i)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /run it anyway/i })).not.toBeInTheDocument();
});

test("surfaces a failed correction instead of looking like it worked", async () => {
  mockPost.mockRejectedValue(new Error("this bioAF has no version of it to move to"));
  render(<DepositConflictNotice studyId={7} conflict={CONFLICT} onChanged={jest.fn()} />);

  await userEvent.click(screen.getByRole("button", { name: /use nf-core\/methylseq instead/i }));

  expect(await screen.findByText(/no version of it to move to/i)).toBeInTheDocument();
});

test("becomes the record of the decision once it has been answered", () => {
  // Found in the browser: the override recorded and the panel went on offering the same two
  // choices, with Approve still hidden. Answered means answered.
  render(
    <DepositConflictNotice
      studyId={7}
      conflict={{ ...CONFLICT, override: { user_id: 3, at: "2026-08-31T12:00:00+00:00", reason: "mislabelled series" } }}
      onChanged={jest.fn()}
    />,
  );
  expect(screen.getByText(/mislabelled series/i)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /run it anyway/i })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /use nf-core/i })).not.toBeInTheDocument();
});
