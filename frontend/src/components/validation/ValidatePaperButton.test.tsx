/**
 * plan_8_7 stage 2: clicking Validate starts the assessment, not a cost decision.
 *
 * The route chooser stood in front of everything. A reader who wanted to know what a paper's evidence
 * says had to first authorize either a deposit reproduction or hours of cluster compute, and discovery
 * is what establishes which of those is even possible, so the question came before its answer.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ValidatePaperButton } from "./ValidatePaperButton";
import { api } from "@/lib/api";

jest.mock("@/lib/api", () => ({ api: { post: jest.fn() } }));
jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock("@/hooks/usePermissions", () => ({ usePermissions: () => ({ canAccess: () => true }) }));
jest.mock("@/hooks/useBetaFeatures", () => ({
  useBetaFeatures: () => ({ flags: { lit_validation: true }, loading: false }),
}));

const post = api.post as jest.Mock;

beforeEach(() => {
  post.mockReset();
  post.mockResolvedValue({ id: 7 });
});

describe("the default action", () => {
  it("starts an assessment with no route question at all", async () => {
    render(<ValidatePaperButton paperId={3} doi="10.1/x" />);
    await userEvent.click(screen.getByRole("button", { name: /validate/i }));
    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(post).toHaveBeenCalledWith("/api/validation-studies", {
      paper_id: 3,
      source_doi: "10.1/x",
      intended_route: "assessment",
    });
  });

  it("does not ask the reader to authorize compute to read a paper", async () => {
    render(<ValidatePaperButton paperId={3} />);
    await userEvent.click(screen.getByRole("button", { name: /validate/i }));
    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(screen.queryByText(/raw reads/i)).not.toBeInTheDocument();
  });
});

describe("reproduction stays an explicit choice", () => {
  it("offers it as an advanced option beside the button", async () => {
    render(<ValidatePaperButton paperId={3} />);
    await userEvent.click(screen.getByRole("button", { name: /reproduce/i }));
    expect(screen.getByText(/raw reads/i)).toBeInTheDocument();
  });

  it("sends the chosen route and says the spend cannot be recovered", async () => {
    render(<ValidatePaperButton paperId={3} />);
    await userEvent.click(screen.getByRole("button", { name: /reproduce/i }));
    await userEvent.click(screen.getByRole("radio", { name: /raw reads/i }));
    expect(screen.getByText(/cannot be recovered/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /start/i }));
    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(post.mock.calls[0][1].intended_route).toBe("pipeline");
  });
});
