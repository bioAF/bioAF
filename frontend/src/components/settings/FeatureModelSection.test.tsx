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

const MODEL_LISTS = {
  model_lists: [
    { provider: "anthropic", models: ["claude-opus-4-8", "claude-sonnet-4-6"], used_fallback: false },
    { provider: "google", models: ["gemini-2.5-pro", "gemini-2.5-flash"], used_fallback: false },
    { provider: "openai", models: ["gpt-5"], used_fallback: false },
    { provider: "gemma", models: ["gemma-4-9b"], used_fallback: false },
  ],
};

/** Serve the feature-models payload and the provider model lists from their own endpoints. */
function serve(features: Record<string, unknown>, providers: Record<string, unknown> = MODEL_LISTS) {
  mockGet.mockImplementation((url: string) =>
    Promise.resolve(url.includes("/feature-models") ? features : providers),
  );
}

beforeEach(() => {
  mockGet.mockReset();
  mockPut.mockReset();
  mockDelete.mockReset();
});

test("shows the model each feature actually runs on", async () => {
  serve(payload());
  render(<FeatureModelSection />);

  expect(await screen.findByText("Literature Validation")).toBeInTheDocument();
  expect(screen.getByText("Literature Review")).toBeInTheDocument();
  expect(screen.getAllByText(/claude-sonnet-4-6/).length).toBeGreaterThan(0);
});

test("says when a feature is running on the org default rather than its own model", async () => {
  serve(payload());
  render(<FeatureModelSection />);

  expect((await screen.findAllByText("org default")).length).toBe(2);
});

test("warns when the model in use probably cannot do the job, and says why", async () => {
  serve(payload({ provider: "gemma", model: "gemma-4-9b", suitability: unlikelySuitability }));
  render(<FeatureModelSection />);

  expect(await screen.findByText(/context window is smaller than a typical full paper/i)).toBeInTheDocument();
  expect(screen.getAllByText(/provisional/i).length).toBeGreaterThan(0);
});

test("does not warn about a model nobody has assessed", async () => {
  serve(payload({ model: "some-new-architecture-1", suitability: unprovenSuitability }));
  render(<FeatureModelSection />);

  // Await the rendered reason, not the fetch: the section resolves two requests before it renders,
  // so a mock-call assertion would run a tick early and see an empty document.
  expect((await screen.findAllByText(/has not assessed this model/i)).length).toBeGreaterThan(0);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("saves an override", async () => {
  serve(payload());
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
  const model = screen.getByLabelText(/literature validation model/i) as HTMLSelectElement;
  await userEvent.selectOptions(model, "gemini-2.5-pro");
  await userEvent.click(screen.getAllByRole("button", { name: /^save$/i })[0]);

  await waitFor(() =>
    expect(mockPut).toHaveBeenCalledWith(
      "/api/integrations/llm/feature-models/literature_validation",
      { provider: "google", model: "gemini-2.5-pro" },
    ),
  );
});

test("shows the server's refusal when the provider has no key", async () => {
  serve(payload());
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
  serve(payload({ provider: "google", model: "gemini-2.5-pro", overridden: true }));
  mockDelete.mockResolvedValue(undefined);
  render(<FeatureModelSection />);

  await userEvent.click(await screen.findByRole("button", { name: /use org default/i }));

  await waitFor(() =>
    expect(mockDelete).toHaveBeenCalledWith(
      "/api/integrations/llm/feature-models/literature_validation",
    ),
  );
});


// ---- the model field is a dropdown, not a free-text box ----
//
// A raw text input asks the user to know model ids from memory and typo them silently: the save
// succeeds, extraction then fails at the provider with an auth-shaped error, and nothing on the
// screen connects that to this field. Every other model picker in Settings is a select fed by the
// provider's own model list, and this one has to match.

test("the model field is a dropdown, not a text box", async () => {
  serve(payload());
  render(<FeatureModelSection />);

  const model = await screen.findByLabelText(/literature validation model/i);
  expect(model.tagName).toBe("SELECT");
});

test("it offers the models the selected provider actually has", async () => {
  serve(payload());
  render(<FeatureModelSection />);

  const model = (await screen.findByLabelText(/literature validation model/i)) as HTMLSelectElement;
  const options = Array.from(model.options).map((o) => o.value);
  expect(options).toEqual(expect.arrayContaining(["claude-opus-4-8", "claude-sonnet-4-6"]));
  expect(options).not.toContain("gemini-2.5-pro");
});

test("choosing another provider offers that provider's models", async () => {
  serve(payload());
  render(<FeatureModelSection />);

  const provider = (await screen.findByLabelText(
    /literature validation provider/i,
  )) as HTMLSelectElement;
  await userEvent.selectOptions(provider, "google");

  const model = screen.getByLabelText(/literature validation model/i) as HTMLSelectElement;
  const options = Array.from(model.options).map((o) => o.value);
  expect(options).toEqual(expect.arrayContaining(["gemini-2.5-pro", "gemini-2.5-flash"]));
  expect(options).not.toContain("claude-opus-4-8");
});

test("the model in use is the one selected", async () => {
  serve(payload());
  render(<FeatureModelSection />);

  const model = (await screen.findByLabelText(/literature validation model/i)) as HTMLSelectElement;
  expect(model.value).toBe("claude-sonnet-4-6");
});

test("a saved model the provider no longer lists is still shown rather than silently dropped", async () => {
  // A model can be retired between saving an override and opening this page. Replacing the user's
  // stored value with the first item in a list would change what runs without telling them.
  serve(payload({ model: "claude-opus-4-1-retired" }));
  render(<FeatureModelSection />);

  const model = (await screen.findByLabelText(/literature validation model/i)) as HTMLSelectElement;
  expect(model.value).toBe("claude-opus-4-1-retired");
  expect(Array.from(model.options).map((o) => o.value)).toContain("claude-opus-4-1-retired");
});

test("it says when the model list is a local fallback rather than the provider's own", async () => {
  serve(payload(), {
    model_lists: [
      { provider: "anthropic", models: ["claude-sonnet-4-6"], used_fallback: true },
    ],
  });
  render(<FeatureModelSection />);

  expect(await screen.findByText(/local fallback/i)).toBeInTheDocument();
});
