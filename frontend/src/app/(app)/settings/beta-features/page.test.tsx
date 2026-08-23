import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import BetaFeaturesPage from "./page";


const mockGet = jest.fn();
const mockPut = jest.fn();
jest.mock("@/lib/api", () => ({
  api: {
    get: (p: string) => mockGet(p),
    put: (p: string, b: unknown) => mockPut(p, b),
  },
}));

beforeEach(() => {
  mockGet.mockReset();
  mockPut.mockReset();
});

describe("BetaFeaturesPage", () => {
  it("renders a labeled toggle per beta feature reflecting its state", async () => {
    mockGet.mockResolvedValue({ flags: { lit_validation: false } });
    render(<BetaFeaturesPage />);
    const toggle = await screen.findByRole("switch", { name: /Literature Validation/i });
    expect(toggle).toHaveAttribute("aria-checked", "false");
  });

  it("PUTs the new value and reflects it when a toggle is clicked", async () => {
    mockGet.mockResolvedValue({ flags: { lit_validation: false } });
    mockPut.mockResolvedValue({ flags: { lit_validation: true } });
    render(<BetaFeaturesPage />);
    const toggle = await screen.findByRole("switch", { name: /Literature Validation/i });
    fireEvent.click(toggle);
    await waitFor(() =>
      expect(mockPut).toHaveBeenCalledWith("/api/beta-features/lit_validation", { enabled: true }),
    );
    await waitFor(() =>
      expect(screen.getByRole("switch", { name: /Literature Validation/i })).toHaveAttribute(
        "aria-checked",
        "true",
      ),
    );
  });

  it("leaves the toggle usable on any instance", async () => {
    // Previously the toggle was disabled and a "not available on this instance" warning shown
    // unless an admin's email ended in @bioaf.co, so a customer admin could see the switch and
    // never move it.
    mockGet.mockResolvedValue({ flags: { lit_validation: false } });
    render(<BetaFeaturesPage />);
    const toggle = await screen.findByRole("switch", { name: /Literature Validation/i });
    expect(toggle).not.toBeDisabled();
    expect(screen.queryByText(/not available on this instance/i)).not.toBeInTheDocument();
  });
});
