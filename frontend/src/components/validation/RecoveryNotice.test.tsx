/**
 * plan_8_2 section 2.1 and owner decision 5: a per-study recovery, on request only.
 *
 * The notice appears when some checks were made under rules bioAF has since replaced. It shows the
 * server's preview (what is reused, fetched and re-evaluated; that no workflow is launched) before anything
 * happens, and sends the preview's fingerprint so the server never does what the person did not see. The
 * labels are pending the owner's sign-off.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { RecoveryNotice } from "./RecoveryNotice";

let allowed = true;
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => allowed, roleName: "admin", loading: false }),
}));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), post: jest.fn() } }));

import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;
const mockPost = api.post as jest.Mock;

const PREVIEW = {
  available: true,
  launches_workflow: false,
  model_calls: 0,
  fingerprint: "fp-1",
  actions: [
    { kind: "fetch_passages", label: "Read the passages that cite each supplement", detail: "From Europe PMC." },
    { kind: "reevaluate_checks", label: "Re-evaluate 3 consistency checks", detail: "Kept in history." },
  ],
  affected_checks: [{ check_id: "plan:1:claim:2:author_results", why: "compared before bioAF established which contrast its table reports" }],
  needs_approval: [{ check_id: "plan:1:claim:2:processed_reanalysis", why: "a new run needs an approval" }],
};

beforeEach(() => {
  allowed = true;
  mockGet.mockReset();
  mockPost.mockReset();
});

test("shows what the recovery will do before doing it, then sends the fingerprint it showed", async () => {
  const onChanged = jest.fn();
  mockGet.mockResolvedValue(PREVIEW);
  mockPost.mockResolvedValue({ id: 5, recovery: { requeued: 3 } });
  render(<RecoveryNotice studyId={5} onChanged={onChanged} />);
  fireEvent.click(screen.getByRole("button", { name: "Review the re-evaluation" }));
  expect(await screen.findByText("Read the passages that cite each supplement")).toBeInTheDocument();
  expect(screen.getByText("Re-evaluate 3 consistency checks")).toBeInTheDocument();
  expect(screen.getByText(/No workflow is launched and no model is asked/)).toBeInTheDocument();
  expect(screen.getByText(/a new run needs an approval/)).toBeInTheDocument();
  expect(mockPost).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole("button", { name: "Re-evaluate" }));
  await waitFor(() => expect(onChanged).toHaveBeenCalledWith({ id: 5, recovery: { requeued: 3 } }));
  expect(mockPost).toHaveBeenCalledWith("/api/validation-studies/5/recovery", { preview_fingerprint: "fp-1" });
});

test("a preview that changed is shown again rather than carried out", async () => {
  mockGet.mockResolvedValue(PREVIEW);
  const changed = Object.assign(new Error("the study changed since the preview was shown; review the recovery again"), {
    status: 409,
  });
  mockPost.mockRejectedValueOnce(changed);
  render(<RecoveryNotice studyId={5} onChanged={jest.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Review the re-evaluation" }));
  fireEvent.click(await screen.findByRole("button", { name: "Re-evaluate" }));
  expect(await screen.findByText(/review the recovery again/)).toBeInTheDocument();
});

test("a person who may only view the study sees no control", () => {
  allowed = false;
  const { container } = render(<RecoveryNotice studyId={5} onChanged={jest.fn()} />);
  expect(container).toBeEmptyDOMElement();
});

// plan_8_2 section 4.1 and decision 4: a paper outside bioAF's methods, classified by an early exit.
test("says the outcome can be restated when that is what the recovery would do", () => {
  render(<RecoveryNotice studyId={46} onChanged={jest.fn()} restate={{ from: "missing_data", to: "inconclusive" }} affectedCount={0} />);
  expect(screen.getByText(/classified missing_data under rules bioAF has since replaced/)).toBeInTheDocument();
  expect(screen.getByText(/can be restated as inconclusive/)).toBeInTheDocument();
  expect(screen.queryByText(/pending re-evaluation/)).not.toBeInTheDocument();
});
