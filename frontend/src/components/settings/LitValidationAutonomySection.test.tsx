/**
 * plan_6 step 5: the autonomy setting, in Settings > Integrations > LLMs.
 *
 * Two modes. The copy has to make clear what actually changes, because the obvious reading of
 * "autonomous" is that it removes the human from the loop, and it does not: the C1 gate stays human
 * in both modes because that is where the spend is authorised.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { LitValidationAutonomySection } from "./LitValidationAutonomySection";

jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn(), put: jest.fn(), delete: jest.fn() },
}));

jest.mock("@/lib/literature", () => {
  const actual = jest.requireActual("@/lib/literature");
  return {
    ...actual,
    literature: {
      ...actual.literature,
      getLitValidationSettings: jest.fn(),
      updateLitValidationSettings: jest.fn(),
    },
  };
});

import { literature } from "@/lib/literature";

const mockGet = literature.getLitValidationSettings as jest.Mock;
const mockUpdate = literature.updateLitValidationSettings as jest.Mock;

beforeEach(() => {
  mockGet.mockReset();
  mockUpdate.mockReset();
});

test("renders the mode the org is actually in", async () => {
  mockGet.mockResolvedValue({ autonomy: "assisted" });
  render(<LitValidationAutonomySection />);

  await waitFor(() => expect(mockGet).toHaveBeenCalled());
  const select = (await screen.findByLabelText(/autonomy/i)) as HTMLSelectElement;
  expect(select.value).toBe("assisted");
});

test("saves the chosen mode", async () => {
  mockGet.mockResolvedValue({ autonomy: "assisted" });
  mockUpdate.mockResolvedValue({ autonomy: "autonomous" });
  render(<LitValidationAutonomySection />);

  const select = (await screen.findByLabelText(/autonomy/i)) as HTMLSelectElement;
  await userEvent.selectOptions(select, "autonomous");
  await userEvent.click(screen.getByRole("button", { name: /save/i }));

  await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith({ autonomy: "autonomous" }));
  expect(await screen.findByText(/saved/i)).toBeInTheDocument();
});

test("says that the approval gate stays human in both modes", async () => {
  mockGet.mockResolvedValue({ autonomy: "assisted" });
  render(<LitValidationAutonomySection />);

  await waitFor(() => expect(mockGet).toHaveBeenCalled());
  // The setting governs the science, not the spend, and the copy must not let anyone believe
  // otherwise before they turn it on.
  expect(screen.getByText(/approve every study before it runs/i)).toBeInTheDocument();
});

test("surfaces a save failure instead of silently keeping the old mode", async () => {
  mockGet.mockResolvedValue({ autonomy: "assisted" });
  mockUpdate.mockRejectedValue(new Error("autonomy must be one of ('assisted', 'autonomous')"));
  render(<LitValidationAutonomySection />);

  const select = (await screen.findByLabelText(/autonomy/i)) as HTMLSelectElement;
  await userEvent.selectOptions(select, "autonomous");
  await userEvent.click(screen.getByRole("button", { name: /save/i }));

  expect(await screen.findByText(/autonomy must be one of/i)).toBeInTheDocument();
});
