/**
 * plan_6 steps 6 and 7: pick a model per feature, and be told when that model probably cannot do
 * the job.
 *
 * The warning informs and never blocks. A banner that stopped the save would be bioAF overruling a
 * lab about its own model, on a curated table with no measurement behind it.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { FeatureModelSection } from "./FeatureModelSection";

jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn(), put: jest.fn(), delete: jest.fn() },
}));

import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;
const mockPut = api.put as jest.Mock;
const mockDelete = api.delete as jest.Mock;

const goodSuitability = {
  verdict: "known_good",
  reason: "holds a full paper in context.",
  warn: false,
  note: "This assessment is provisional.",
  blocks_save: false,
};

const unlikelySuitability = {
  verdict: "unlikely",
  reason:
    "this model's context window is smaller than a typical full paper, so extraction will fail on most papers.",
  warn: true,
  note: "This assessment is provisional.",
  blocks_save: false,
};

const unprovenSuitability = {
  verdict: "unproven",
  reason: "bioAF has not assessed this model for literature validation.",
  warn: false,
  note: "This assessment is provisional.",
  blocks_save: false,
};

function payload(over: Record<string, unknown> = {}) {
  return {
    features: [
      {
        feature: "literature_validation",
        provider: "anthropic",
        model: "claude-sonnet-4-6",
        overridden: false,
        suitability: goodSuitability,
        ...over,
      },
      {
        feature: "literature_review",
        provider: "anthropic",
        model: "claude-sonnet-4-6",
        overridden: false,
        suitability: goodSuitability,
      },
    ],
  };
}

beforeEach(() => {
  mockGet.mockReset();
  mockPut.mockReset();
  mockDelete.mockReset();
});

test("shows the model each feature actually runs on", async () => {
  mockGet.mockResolvedValue(payload());
  render(<FeatureModelSection />);

  expect(await screen.findByText("Literature Validation")).toBeInTheDocument();
  expect(screen.getByText("Literature Review")).toBeInTheDocument();
  expect(screen.getAllByText(/claude-sonnet-4-6/).length).toBeGreaterThan(0);
});

test("says when a feature is running on the org default rather than its own model", async () => {
  mockGet.mockResolvedValue(payload());
  render(<FeatureModelSection />);

  expect((await screen.findAllByText("org default")).length).toBe(2);
});

test("warns when the model in use probably cannot do the job, and says why", async () => {
  mockGet.mockResolvedValue(
    payload({ provider: "gemma", model: "gemma-4-9b", suitability: unlikelySuitability }),
  );
  render(<FeatureModelSection />);

  expect(await screen.findByText(/context window is smaller than a typical full paper/i)).toBeInTheDocument();
  expect(screen.getAllByText(/provisional/i).length).toBeGreaterThan(0);
});

test("does not warn about a model nobody has assessed", async () => {
  mockGet.mockResolvedValue(
    payload({ model: "some-new-architecture-1", suitability: unprovenSuitability }),
  );
  render(<FeatureModelSection />);

  await waitFor(() => expect(mockGet).toHaveBeenCalled());
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.getAllByText(/has not assessed this model/i).length).toBeGreaterThan(0);
});

test("saves an override", async () => {
  mockGet.mockResolvedValue(payload());
  mockPut.mockResolvedValue({
    feature: "literature_validation",
    provider: "google",
    model: "gemini-2.5-pro",
    overridden: true,
    suitability: goodSuitability,
  });
  render(<FeatureModelSection />);

  const provider = (await screen.findByLabelText(
    /literature validation provider/i,
  )) as HTMLSelectElement;
  await userEvent.selectOptions(provider, "google");
  const model = screen.getByLabelText(/literature validation model/i) as HTMLInputElement;
  await userEvent.clear(model);
  await userEvent.type(model, "gemini-2.5-pro");
  await userEvent.click(screen.getAllByRole("button", { name: /^save$/i })[0]);

  await waitFor(() =>
    expect(mockPut).toHaveBeenCalledWith(
      "/api/integrations/llm/feature-models/literature_validation",
      { provider: "google", model: "gemini-2.5-pro" },
    ),
  );
});

test("shows the server's refusal when the provider has no key", async () => {
  mockGet.mockResolvedValue(payload());
  mockPut.mockRejectedValue(new Error("The google provider needs an API key before a feature can use it."));
  render(<FeatureModelSection />);

  const provider = (await screen.findByLabelText(
    /literature validation provider/i,
  )) as HTMLSelectElement;
  await userEvent.selectOptions(provider, "google");
  await userEvent.click(screen.getAllByRole("button", { name: /^save$/i })[0]);

  expect(await screen.findByText(/needs an API key/i)).toBeInTheDocument();
});

test("can return a feature to the org default", async () => {
  mockGet.mockResolvedValue(
    payload({ provider: "google", model: "gemini-2.5-pro", overridden: true }),
  );
  mockDelete.mockResolvedValue(undefined);
  render(<FeatureModelSection />);

  await userEvent.click(await screen.findByRole("button", { name: /use org default/i }));

  await waitFor(() =>
    expect(mockDelete).toHaveBeenCalledWith(
      "/api/integrations/llm/feature-models/literature_validation",
    ),
  );
});
