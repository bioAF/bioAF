/**
 * Validate starts the ASSESSMENT; reproducing the results is the advanced choice beside it.
 *
 * Three rounds of owner decisions are held here, each flagged per ai_guides/tdd.md:
 *
 * 1. 2026-09-08: the button was "Validate reproduction" and is now "Validate findings". It named the
 *    mechanism; what a scientist wants validated is the paper's findings.
 * 2. 2026-09-08: clicking used to POST immediately. It then opened the route dialog, because the old
 *    behaviour created a study in `requested` and dropped the reader on a page reading "Step 1 of 9"
 *    with an in-progress badge, while two further clicks stood between it and any work.
 * 3. plan_8_7 stage 2: clicking POSTS immediately again, with `assessment`, and the route dialog
 *    moved to its own control. The dialog was asking the reader to authorize either a deposit
 *    reproduction or hours of cluster compute BEFORE bioAF had read the paper, and discovery is what
 *    establishes which of those is even possible. An assessment spends nothing, so nothing has to be
 *    authorized to start one; the routes keep their chooser, their warnings and their behaviour.
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
  await userEvent.click(screen.getByRole("button", { name: /reproduce results too/i }));
}

describe("ValidatePaperButton", () => {
  it("is called 'Validate findings'", () => {
    render(<ValidatePaperButton paperId={9} doi="10.1/x" />);
    expect(screen.getByRole("button", { name: /validate findings/i })).toBeInTheDocument();
  });

  it("starts the assessment as soon as it is clicked, with no route question", async () => {
    mockPost.mockResolvedValue({ id: 41, state: "requested" });
    render(<ValidatePaperButton paperId={9} doi="10.1/x" />);
    await userEvent.click(screen.getByRole("button", { name: /validate findings/i }));

    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith("/api/validation-studies", {
        paper_id: 9,
        source_doi: "10.1/x",
        intended_route: "assessment",
      }),
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("creates nothing until the reader has chosen a route for a reproduction", async () => {
    render(<ValidatePaperButton paperId={9} doi="10.1/x" />);
    await openDialog();

    expect(mockPost).not.toHaveBeenCalled();
    expect(mockPush).not.toHaveBeenCalled();
  });

  it("asks what to reproduce when the advanced control is used", async () => {
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

  it("says the assessment runs either way, so the choice is about the reproduction", async () => {
    render(<ValidatePaperButton paperId={9} doi="10.1/x" />);
    await openDialog();

    expect(screen.getByText(/assessment of the paper.s evidence runs either way/i)).toBeInTheDocument();
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
