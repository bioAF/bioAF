import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RetryNotice } from "./RetryNotice";

let canAccessResult = true;
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => canAccessResult, roleName: "admin", loading: false, permissions: new Set() }),
}));

jest.mock("@/lib/api", () => ({ api: { post: jest.fn() } }));
import { api } from "@/lib/api";
const mockPost = api.post as jest.Mock;

beforeEach(() => {
  canAccessResult = true;
  mockPost.mockReset();
  mockPost.mockResolvedValue({ id: 11, state: "setup" });
});

const REASON = "analysis run failed";

test("says what failed, in the words the study recorded", () => {
  render(<RetryNotice studyId={11} failureReason={REASON} onChanged={jest.fn()} />);
  expect(screen.getByText(/analysis run failed/i)).toBeInTheDocument();
});

test("distinguishes an infrastructure failure from a verdict about the paper", () => {
  // The whole point of the state: `error` is not "this paper did not reproduce".
  render(<RetryNotice studyId={11} failureReason={REASON} onChanged={jest.fn()} />);
  expect(screen.getByText(/not a (result|verdict|judgment)/i)).toBeInTheDocument();
});

test("Retry calls the retry endpoint and hands back the updated study", async () => {
  const onChanged = jest.fn();
  render(<RetryNotice studyId={11} failureReason={REASON} onChanged={onChanged} />);

  await userEvent.click(screen.getByRole("button", { name: /retry/i }));

  await waitFor(() => expect(mockPost).toHaveBeenCalledTimes(1));
  expect(mockPost).toHaveBeenCalledWith("/api/validation-studies/11/retry", {});
  expect(onChanged).toHaveBeenCalledWith({ id: 11, state: "setup" });
});

test("surfaces a failed retry instead of looking like it worked", async () => {
  mockPost.mockRejectedValue(new Error("Only a study in 'error' can be retried"));
  render(<RetryNotice studyId={11} failureReason={REASON} onChanged={jest.fn()} />);

  await userEvent.click(screen.getByRole("button", { name: /retry/i }));

  expect(await screen.findByText(/can be retried/i)).toBeInTheDocument();
});

test("hides the control from someone who cannot approve compute", () => {
  canAccessResult = false;
  render(<RetryNotice studyId={11} failureReason={REASON} onChanged={jest.fn()} />);
  expect(screen.queryByRole("button", { name: /retry/i })).not.toBeInTheDocument();
  // The explanation still shows: a viewer should understand why the study stopped.
  expect(screen.getByText(/analysis run failed/i)).toBeInTheDocument();
});

// The retry window is what makes retry a real choice rather than an open-ended one: the fetched
// data is kept for a few days and then deleted, so the notice has to say which side of that line
// the study is on.

test("says how long the downloaded data will still be there", () => {
  const reapAfter = "2026-09-05T10:00:00+00:00";
  render(
    <RetryNotice studyId={11} failureReason={REASON} reapAfter={reapAfter} onChanged={jest.fn()} />,
  );
  const deadline = new Date(reapAfter).toLocaleDateString();
  expect(screen.getByText(new RegExp(deadline.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")))).toBeInTheDocument();
});

test("says plainly when the data is already gone, so a retry is not mistaken for a resume", () => {
  render(
    <RetryNotice studyId={11} failureReason={REASON} dataDeleted onChanged={jest.fn()} />,
  );
  expect(screen.getByText(/has been deleted/i)).toBeInTheDocument();
  expect(screen.getByText(/download the data again/i)).toBeInTheDocument();
});

test("says nothing about a deadline when there is no data to expire", () => {
  render(<RetryNotice studyId={11} failureReason={REASON} onChanged={jest.fn()} />);
  expect(screen.queryByText(/deleted/i)).not.toBeInTheDocument();
});
