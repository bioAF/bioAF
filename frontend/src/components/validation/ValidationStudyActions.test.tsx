import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ValidationStudyActions } from "./ValidationStudyActions";

jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, roleName: "admin", loading: false, permissions: new Set() }),
}));

jest.mock("@/lib/api", () => ({ api: { post: jest.fn() } }));
import { api } from "@/lib/api";
const mockPost = api.post as jest.Mock;

beforeEach(() => {
  mockPost.mockReset();
});

// The server's own words. A plan naming a pipeline that cannot read the data the study is scoped to
// is refused at approve time, and the refusal is one plain sentence naming both sides.
const CONFLICT =
  "nf-core/atacseq does not consume Bisulfite-Seq data, and the accession this study was scoped to " +
  "is deposited as Bisulfite-Seq. Running it would spend the compute and answer confidently about " +
  "the wrong thing. nf-core/methylseq is the pipeline for Bisulfite-Seq data.";

test("a refused approval says which pipeline and which data, on screen", async () => {
  mockPost.mockRejectedValue(new Error(CONFLICT));
  const onChanged = jest.fn();
  render(<ValidationStudyActions study={{ id: 7, state: "plan_ready" }} onChanged={onChanged} />);

  await userEvent.click(screen.getByRole("button", { name: /^approve/i }));
  await userEvent.click(screen.getByRole("button", { name: /approve and run/i }));

  await waitFor(() => expect(mockPost).toHaveBeenCalledWith("/api/validation-studies/7/approve", undefined));
  expect(await screen.findByText(/nf-core\/atacseq/)).toBeInTheDocument();
  expect(screen.getByText(/Bisulfite-Seq/)).toBeInTheDocument();
  expect(screen.getByText(/nf-core\/methylseq/)).toBeInTheDocument();
  // The study has not moved: a refusal is not a state change.
  expect(onChanged).not.toHaveBeenCalled();
});

test("an approval the server accepts hands back the updated study", async () => {
  mockPost.mockResolvedValue({ id: 7, state: "acquiring_data" });
  const onChanged = jest.fn();
  render(<ValidationStudyActions study={{ id: 7, state: "plan_ready" }} onChanged={onChanged} />);

  await userEvent.click(screen.getByRole("button", { name: /^approve/i }));
  await userEvent.click(screen.getByRole("button", { name: /approve and run/i }));

  await waitFor(() => expect(onChanged).toHaveBeenCalledWith({ id: 7, state: "acquiring_data" }));
});

// A study back at the approval gate after a retry is not the same decision as a first approval:
// its data was already downloaded once and deleted, so approving pays for the download again.

test("warns that approving re-downloads when the study is back from a retry", () => {
  render(
    <ValidationStudyActions
      study={{ id: 7, state: "plan_ready", evidence: { awaiting_refetch_approval: true } }}
      onChanged={jest.fn()}
    />,
  );
  expect(screen.getByText(/download/i)).toBeInTheDocument();
  expect(screen.getByText(/again/i)).toBeInTheDocument();
});

test("says nothing about re-downloading on a study that never ran", () => {
  render(<ValidationStudyActions study={{ id: 7, state: "plan_ready" }} onChanged={jest.fn()} />);
  expect(screen.queryByText(/download the data again/i)).not.toBeInTheDocument();
});
