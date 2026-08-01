import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SamplesMismatchNotice } from "./SamplesMismatchNotice";

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
  mockPost.mockResolvedValue({ id: 5, state: "setup" });
});

const REASON = "Held before spending compute: these picked samples were not fetched: SRX3. Fetched samples available: GSM_A.";

test("shows what is missing in plain language", () => {
  render(<SamplesMismatchNotice studyId={5} failureReason={REASON} onChanged={jest.fn()} />);
  expect(screen.getByText(/SRX3/)).toBeInTheDocument();
  expect(screen.getByText(/GSM_A/)).toBeInTheDocument();
});

test("'Run anyway' calls the override endpoint and hands back the updated study", async () => {
  const onChanged = jest.fn();
  render(<SamplesMismatchNotice studyId={5} failureReason={REASON} onChanged={onChanged} />);

  await userEvent.click(screen.getByRole("button", { name: /run anyway/i }));

  await waitFor(() => expect(mockPost).toHaveBeenCalledTimes(1));
  expect(mockPost).toHaveBeenCalledWith("/api/validation-studies/5/override-samples", {});
  expect(onChanged).toHaveBeenCalledWith({ id: 5, state: "setup" });
});

test("'Stop' declines the study with a reason", async () => {
  mockPost.mockResolvedValue({ id: 5, state: "plan_declined" });
  const onChanged = jest.fn();
  render(<SamplesMismatchNotice studyId={5} failureReason={REASON} onChanged={onChanged} />);

  await userEvent.click(screen.getByRole("button", { name: /stop/i }));

  await waitFor(() => expect(mockPost).toHaveBeenCalledTimes(1));
  const [url, body] = mockPost.mock.calls[0];
  expect(url).toBe("/api/validation-studies/5/decline");
  expect(body.reason).toMatch(/sample/i);
  expect(onChanged).toHaveBeenCalled();
});

test("a non-approver sees the notice but not the actions", () => {
  canAccessResult = false;
  render(<SamplesMismatchNotice studyId={5} failureReason={REASON} onChanged={jest.fn()} />);
  expect(screen.getByText(/SRX3/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /run anyway/i })).not.toBeInTheDocument();
});
