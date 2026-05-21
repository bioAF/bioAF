import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AutoLitReviewSection } from "./LlmSettingsContent";

jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn(), put: jest.fn(), delete: jest.fn() },
}));

jest.mock("@/lib/literature", () => {
  const actual = jest.requireActual("@/lib/literature");
  return {
    ...actual,
    literature: {
      ...actual.literature,
      getLitReviewSettings: jest.fn(),
      updateLitReviewSettings: jest.fn(),
    },
  };
});

import { literature } from "@/lib/literature";

const mockGet = literature.getLitReviewSettings as jest.Mock;
const mockUpdate = literature.updateLitReviewSettings as jest.Mock;

beforeEach(() => {
  mockGet.mockReset();
  mockUpdate.mockReset();
});

const settings = {
  relevance_threshold: 0.65,
  auto_enabled: false,
  auto_cadence: "weekly",
  max_runs_per_tick: 5,
  next_run: null,
};

test("loads and renders current automation settings", async () => {
  mockGet.mockResolvedValue(settings);
  render(<AutoLitReviewSection />);

  await waitFor(() => expect(mockGet).toHaveBeenCalled());
  expect(screen.getByText("Automated AI Literature Review")).toBeInTheDocument();
  const checkbox = screen.getByRole("checkbox") as HTMLInputElement;
  expect(checkbox.checked).toBe(false);
  expect((screen.getByDisplayValue("Weekly") as HTMLOptionElement)).toBeTruthy();
});

test("saving sends the edited enable, cadence, cap, and first_run", async () => {
  mockGet.mockResolvedValue(settings);
  mockUpdate.mockResolvedValue({
    ...settings,
    auto_enabled: true,
    auto_cadence: "daily",
    max_runs_per_tick: 3,
    next_run: "2099-01-02T09:30:00+00:00",
  });
  render(<AutoLitReviewSection />);
  await waitFor(() => expect(mockGet).toHaveBeenCalled());

  await userEvent.click(screen.getByRole("checkbox"));
  await userEvent.selectOptions(screen.getByRole("combobox"), "daily");
  const capInput = screen.getByRole("spinbutton");
  await userEvent.clear(capInput);
  await userEvent.type(capInput, "3");
  await userEvent.click(screen.getByRole("button", { name: /save/i }));

  await waitFor(() => expect(mockUpdate).toHaveBeenCalled());
  const payload = mockUpdate.mock.calls[0][0];
  expect(payload).toMatchObject({
    auto_enabled: true,
    auto_cadence: "daily",
    max_runs_per_tick: 3,
  });
  // When enabled, an ISO first_run is included from the date/time picker.
  expect(typeof payload.first_run).toBe("string");
  expect(() => new Date(payload.first_run).toISOString()).not.toThrow();
});

test("renders a first-run datetime picker", async () => {
  mockGet.mockResolvedValue({ ...settings, auto_enabled: true });
  render(<AutoLitReviewSection />);
  await waitFor(() => expect(mockGet).toHaveBeenCalled());
  expect(screen.getByText("First run")).toBeInTheDocument();
});

test("rejects a cap below 1 without calling the API", async () => {
  mockGet.mockResolvedValue({ ...settings, auto_enabled: true });
  render(<AutoLitReviewSection />);
  await waitFor(() => expect(mockGet).toHaveBeenCalled());

  const capInput = screen.getByRole("spinbutton");
  await userEvent.clear(capInput);
  await userEvent.type(capInput, "0");
  await userEvent.click(screen.getByRole("button", { name: /save/i }));

  expect(await screen.findByText(/at least 1/i)).toBeInTheDocument();
  expect(mockUpdate).not.toHaveBeenCalled();
});
