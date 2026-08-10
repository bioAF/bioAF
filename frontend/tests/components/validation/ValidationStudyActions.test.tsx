import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { ValidationStudyActions } from "@/components/validation/ValidationStudyActions";

let canAccessImpl = (_r: string, _a: string) => true;
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: (r: string, a: string) => canAccessImpl(r, a), loading: false }),
}));

jest.mock("@/lib/api", () => ({ api: { post: jest.fn() } }));
import { api } from "@/lib/api";
const mockPost = api.post as jest.Mock;

beforeEach(() => {
  mockPost.mockReset();
  mockPost.mockResolvedValue({ id: 3, state: "plan_ready" });
  canAccessImpl = () => true;
});

describe("ValidationStudyActions", () => {
  it("offers Read at requested and posts to /read", async () => {
    const onChanged = jest.fn();
    render(<ValidationStudyActions study={{ id: 3, state: "requested" }} onChanged={onChanged} />);

    fireEvent.click(screen.getByRole("button", { name: /read paper/i }));

    await waitFor(() => expect(mockPost).toHaveBeenCalledWith("/api/validation-studies/3/read", expect.anything()));
    expect(onChanged).toHaveBeenCalled();
  });

  it("offers Approve and Decline at plan_ready and posts to /approve once confirmed", async () => {
    const onChanged = jest.fn();
    render(<ValidationStudyActions study={{ id: 3, state: "plan_ready" }} onChanged={onChanged} />);

    expect(screen.getByRole("button", { name: /decline/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^approve plan$/i }));

    // Approving starts a real, billable pipeline run, so it is confirmed first.
    expect(mockPost).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /approve and run/i }));

    await waitFor(() => expect(mockPost).toHaveBeenCalledWith("/api/validation-studies/3/approve", undefined));
    expect(onChanged).toHaveBeenCalled();
  });

  it("does not spend compute if the approve confirmation is dismissed", async () => {
    const onChanged = jest.fn();
    render(<ValidationStudyActions study={{ id: 3, state: "plan_ready" }} onChanged={onChanged} />);

    fireEvent.click(screen.getByRole("button", { name: /^approve plan$/i }));
    fireEvent.click(screen.getByRole("button", { name: /^cancel$/i }));

    expect(mockPost).not.toHaveBeenCalled();
    expect(onChanged).not.toHaveBeenCalled();
  });

  it("offers the six classification buckets at comparing and posts the chosen one", async () => {
    const onChanged = jest.fn();
    render(<ValidationStudyActions study={{ id: 3, state: "comparing" }} onChanged={onChanged} />);

    fireEvent.change(screen.getByRole("combobox"), { target: { value: "not_validated" } });
    fireEvent.click(screen.getByRole("button", { name: /record classification/i }));

    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith("/api/validation-studies/3/classify", { classification: "not_validated" }),
    );
    expect(onChanged).toHaveBeenCalled();
  });

  it("pre-selects the classifier's suggested verdict in the Classify control", () => {
    render(
      <ValidationStudyActions study={{ id: 3, state: "comparing" }} onChanged={jest.fn()} suggestedClassification="inconclusive" />,
    );
    expect((screen.getByRole("combobox") as HTMLSelectElement).value).toBe("inconclusive");
  });

  it("shows nothing actionable while an automated stage is running", () => {
    render(<ValidationStudyActions study={{ id: 3, state: "running" }} onChanged={jest.fn()} />);
    expect(screen.queryByRole("button", { name: /read paper/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^approve/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /record classification/i })).not.toBeInTheDocument();
  });

  it("hides the approve/decline gate from a user without the approve permission", () => {
    canAccessImpl = (r, a) => !(r === "lit_validation" && a === "approve");
    render(<ValidationStudyActions study={{ id: 3, state: "plan_ready" }} onChanged={jest.fn()} />);
    expect(screen.queryByRole("button", { name: /^approve/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /decline/i })).not.toBeInTheDocument();
  });
});
