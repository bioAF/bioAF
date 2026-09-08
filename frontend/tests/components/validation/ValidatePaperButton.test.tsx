/**
 * The route is chosen at the button now, not at the C1 gate.
 *
 * Two assertions here CHANGED, and both were owner decisions on 2026-09-08 rather than drift, so
 * they are flagged per ai_guides/tdd.md:
 *
 * 1. The button was "Validate reproduction" and is now "Validate findings". It named the mechanism;
 *    what a scientist wants validated is the paper's findings.
 * 2. Clicking used to POST immediately. It now opens the route dialog and creates nothing until the
 *    reader chooses. The old behaviour created a study in `requested` and dropped the reader on a
 *    page reading "Step 1 of 9" with an in-progress badge, while TWO further clicks ("Read paper",
 *    then "Approve") stood between it and any work. Both looked like progress, so a study could sit
 *    untouched while the page implied it was running.
 *
 * The permission gate, the beta gate and the error path are unchanged and still held below.
 */

import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ValidatePaperButton } from "@/components/validation/ValidatePaperButton";

const mockPush = jest.fn();
jest.mock("next/navigation", () => ({ useRouter: () => ({ push: mockPush }) }));

let canAccessImpl = (_r: string, _a: string) => true;
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: (r: string, a: string) => canAccessImpl(r, a), loading: false }),
}));

let betaFlags: Record<string, boolean> = { lit_validation: true };
jest.mock("@/hooks/useBetaFeatures", () => ({
  useBetaFeatures: () => ({ available: true, flags: betaFlags, loading: false }),
}));

jest.mock("@/lib/api", () => ({ api: { post: jest.fn() } }));
import { api } from "@/lib/api";
const mockPost = api.post as jest.Mock;

beforeEach(() => {
  mockPost.mockReset();
  mockPush.mockReset();
  canAccessImpl = () => true;
  betaFlags = { lit_validation: true };
});

async function openDialog() {
  await userEvent.click(screen.getByRole("button", { name: /validate findings/i }));
}

describe("ValidatePaperButton", () => {
  it("is called 'Validate findings'", () => {
    render(<ValidatePaperButton paperId={9} doi="10.1/x" />);
    expect(screen.getByRole("button", { name: /validate findings/i })).toBeInTheDocument();
  });

  it("creates nothing until the reader has chosen a route", async () => {
    render(<ValidatePaperButton paperId={9} doi="10.1/x" />);
    await openDialog();

    expect(mockPost).not.toHaveBeenCalled();
    expect(mockPush).not.toHaveBeenCalled();
  });

  it("asks what to validate as soon as it is clicked", async () => {
    render(<ValidatePaperButton paperId={9} doi="10.1/x" />);
    await openDialog();

    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Deposited data and available code/i })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Raw reads/i })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Both/i })).toBeInTheDocument();
  });

  it("creates the study carrying the chosen route, then navigates to it", async () => {
    mockPost.mockResolvedValue({ id: 42, state: "requested" });
    render(<ValidatePaperButton paperId={9} doi="10.1/x" />);
    await openDialog();
    await userEvent.click(screen.getByRole("button", { name: /start validation/i }));

    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith("/api/validation-studies", {
        paper_id: 9,
        source_doi: "10.1/x",
        intended_route: "deposit",
      }),
    );
    await waitFor(() => expect(mockPush).toHaveBeenCalledWith("/lab-knowledge/validation-studies/42"));
  });

  it("sends the raw-reads route when that is the one picked", async () => {
    mockPost.mockResolvedValue({ id: 43, state: "requested" });
    render(<ValidatePaperButton paperId={9} doi="10.1/x" />);
    await openDialog();
    await userEvent.click(screen.getByRole("radio", { name: /Raw reads/i }));
    await userEvent.click(screen.getByRole("button", { name: /start validation/i }));

    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith(
        "/api/validation-studies",
        expect.objectContaining({ intended_route: "pipeline" }),
      ),
    );
  });

  it("warns that the raw-reads route spends real compute before it is started", async () => {
    render(<ValidatePaperButton paperId={9} doi="10.1/x" />);
    await openDialog();
    await userEvent.click(screen.getByRole("radio", { name: /Raw reads/i }));

    expect(screen.getByText(/spends compute on your cloud account/i)).toBeInTheDocument();
  });

  it("says the study runs itself after the choice, so waiting never looks like nothing happening", async () => {
    render(<ValidatePaperButton paperId={9} doi="10.1/x" />);
    await openDialog();

    expect(screen.getByText(/reads the paper and starts on its own/i)).toBeInTheDocument();
  });

  it("creates nothing when the dialog is cancelled", async () => {
    render(<ValidatePaperButton paperId={9} doi="10.1/x" />);
    await openDialog();
    await userEvent.click(screen.getByRole("button", { name: /cancel/i }));

    expect(mockPost).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("renders nothing for a user without the request permission", () => {
    canAccessImpl = (r, a) => !(r === "lit_validation" && a === "request");
    const { container } = render(<ValidatePaperButton paperId={9} doi="10.1/x" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when the lit_validation beta flag is off, even with permission", () => {
    betaFlags = {};
    const { container } = render(<ValidatePaperButton paperId={9} doi="10.1/x" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("surfaces an error without navigating when creation fails", async () => {
    mockPost.mockRejectedValue(new Error("boom"));
    render(<ValidatePaperButton paperId={9} doi={null} />);
    await openDialog();
    fireEvent.click(screen.getByRole("button", { name: /start validation/i }));

    await waitFor(() => expect(screen.getByText("boom")).toBeInTheDocument());
    expect(mockPush).not.toHaveBeenCalled();
  });
});
