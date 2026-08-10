import { render, screen, waitFor, fireEvent } from "@testing-library/react";
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

describe("ValidatePaperButton", () => {
  it("creates a study from the paper and navigates to its detail page", async () => {
    mockPost.mockResolvedValue({ id: 42, state: "requested" });
    render(<ValidatePaperButton paperId={9} doi="10.1/x" />);

    fireEvent.click(screen.getByRole("button", { name: /validate reproduction/i }));

    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith("/api/validation-studies", { paper_id: 9, source_doi: "10.1/x" }),
    );
    await waitFor(() => expect(mockPush).toHaveBeenCalledWith("/lab-knowledge/validation-studies/42"));
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

    fireEvent.click(screen.getByRole("button", { name: /validate reproduction/i }));

    await waitFor(() => expect(screen.getByText("boom")).toBeInTheDocument());
    expect(mockPush).not.toHaveBeenCalled();
  });
});
