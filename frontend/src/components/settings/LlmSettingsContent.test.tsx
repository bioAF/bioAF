import { render, screen } from "@testing-library/react";
import { LlmSettingsContent } from "./LlmSettingsContent";

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

import { api } from "@/lib/api";
import { literature } from "@/lib/literature";

const mockGet = api.get as jest.Mock;
const mockLit = literature.getLitReviewSettings as jest.Mock;

beforeEach(() => {
  mockGet.mockReset();
  mockLit.mockReset();
  mockLit.mockResolvedValue({
    relevance_threshold: 0.65,
    auto_enabled: false,
    auto_cadence: "weekly",
    max_runs_per_tick: 5,
    next_run: null,
  });
});

function providers(overrides: Record<string, unknown>) {
  return {
    configs: [
      { provider: "anthropic", model: "claude-x", api_key_prefix_last5: "AB", is_active: true, configured: true },
    ],
    active_provider: "anthropic",
    model_lists: [{ provider: "anthropic", models: ["claude-x"], used_fallback: false }],
    ...overrides,
  };
}

test("shows admin guidance that the active model also powers the tool-using assistant", async () => {
  mockGet.mockResolvedValue(providers({}));
  render(<LlmSettingsContent />);
  const guidance = await screen.findByTestId("assistant-model-guidance");
  expect(guidance).toBeInTheDocument();
  expect(guidance).toHaveTextContent(/assistant/i);
});

test("warns when the active provider cannot power the assistant (not tool-capable)", async () => {
  mockGet.mockResolvedValue(providers({ active_provider: "gemma" }));
  render(<LlmSettingsContent />);
  expect(await screen.findByTestId("active-not-tool-capable")).toBeInTheDocument();
});

test("no not-tool-capable warning when the active provider is tool-capable", async () => {
  mockGet.mockResolvedValue(providers({ active_provider: "anthropic" }));
  render(<LlmSettingsContent />);
  await screen.findByTestId("assistant-model-guidance");
  expect(screen.queryByTestId("active-not-tool-capable")).not.toBeInTheDocument();
});
