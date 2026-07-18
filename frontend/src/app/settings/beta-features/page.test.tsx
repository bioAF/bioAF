import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import BetaFeaturesPage from "./page";

jest.mock("@/components/layout/Sidebar", () => ({ Sidebar: () => <div data-testid="sidebar" /> }));
jest.mock("@/components/layout/Header", () => ({ Header: () => <div data-testid="header" /> }));

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
    mockGet.mockResolvedValue({ available: true, flags: { lit_validation: false } });
    render(<BetaFeaturesPage />);
    const toggle = await screen.findByRole("switch", { name: /Literature Validation/i });
    expect(toggle).toHaveAttribute("aria-checked", "false");
  });

  it("PUTs the new value and reflects it when a toggle is clicked", async () => {
    mockGet.mockResolvedValue({ available: true, flags: { lit_validation: false } });
    mockPut.mockResolvedValue({ available: true, flags: { lit_validation: true } });
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

  it("disables the toggle and warns when beta is not available on this instance", async () => {
    mockGet.mockResolvedValue({ available: false, flags: { lit_validation: false } });
    render(<BetaFeaturesPage />);
    const toggle = await screen.findByRole("switch", { name: /Literature Validation/i });
    expect(toggle).toBeDisabled();
    expect(screen.getByText(/not available on this instance/i)).toBeInTheDocument();
  });
});
