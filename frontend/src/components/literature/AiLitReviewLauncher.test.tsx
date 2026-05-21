import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AiLitReviewLauncher } from "./AiLitReviewLauncher";

jest.mock("@/lib/auth", () => ({
  getCurrentUser: () => ({ role_name: "admin" }),
}));

jest.mock("@/lib/api", () => ({
  api: {
    get: jest.fn(),
    post: jest.fn(),
  },
}));

jest.mock("@/lib/literature", () => {
  const actual = jest.requireActual("@/lib/literature");
  return {
    ...actual,
    literature: {
      ...actual.literature,
      runLitReview: jest.fn(),
      getRun: jest.fn(),
    },
  };
});

import { api } from "@/lib/api";
import { literature } from "@/lib/literature";

const mockGet = api.get as jest.Mock;
const mockRun = literature.runLitReview as jest.Mock;
const mockGetRun = literature.getRun as jest.Mock;

const projects = [
  { id: 11, name: "Atlas of TGF-beta" },
  { id: 22, name: "Spatial Map" },
];

const experiments = [
  {
    id: 101,
    name: "Exp One",
    project: { id: 11, name: "Atlas of TGF-beta" },
  },
  {
    id: 102,
    name: "Exp Two",
    project: { id: 22, name: "Spatial Map" },
  },
  {
    id: 103,
    name: "Free-floating Exp",
    project: null,
  },
];

beforeEach(() => {
  mockGet.mockReset();
  mockRun.mockReset();
  mockGetRun.mockReset();
  mockGet.mockImplementation((url: string) => {
    if (url.includes("/api/projects")) {
      return Promise.resolve({ projects, total: projects.length });
    }
    if (url.includes("/api/experiments")) {
      return Promise.resolve({
        experiments,
        total: experiments.length,
        page: 1,
        page_size: 100,
      });
    }
    return Promise.resolve({});
  });
});

test("renders a project select and an experiment select", async () => {
  render(<AiLitReviewLauncher onSubmitted={() => {}} />);
  await waitFor(() => {
    expect(screen.getByLabelText(/project/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/experiment/i)).toBeInTheDocument();
  });
});

test("experiment options include parent-project breadcrumb when no project filter is set", async () => {
  render(<AiLitReviewLauncher onSubmitted={() => {}} />);
  await waitFor(() => {
    expect(screen.getByLabelText(/experiment/i)).toBeInTheDocument();
  });
  // With no project filter, options should include the breadcrumb form.
  expect(screen.getByText("Atlas of TGF-beta > Exp One")).toBeInTheDocument();
  expect(screen.getByText("Spatial Map > Exp Two")).toBeInTheDocument();
  // Experiments with no project show just the name.
  expect(screen.getByText("Free-floating Exp")).toBeInTheDocument();
});

test("project filter narrows the experiment list", async () => {
  const user = userEvent.setup();
  render(<AiLitReviewLauncher onSubmitted={() => {}} />);
  const projectSelect = await screen.findByLabelText(/project/i);
  await user.selectOptions(projectSelect, "11");

  await waitFor(() => {
    // Only Exp One should remain because the project filter is 11.
    expect(screen.getByText(/exp one/i)).toBeInTheDocument();
    expect(screen.queryByText(/exp two/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/free-floating exp/i)).not.toBeInTheDocument();
  });
});

test("submit is disabled until an experiment is chosen", async () => {
  const user = userEvent.setup();
  render(<AiLitReviewLauncher onSubmitted={() => {}} />);
  const button = await screen.findByRole("button", { name: /run/i });
  expect(button).toBeDisabled();

  const expSelect = await screen.findByLabelText(/experiment/i);
  await user.selectOptions(expSelect, "103");
  expect(button).not.toBeDisabled();
});

test("submitting calls runLitReview with the chosen experiment id", async () => {
  mockRun.mockResolvedValue({
    id: 555,
    experiment_id: 103,
    status: "queued",
    recommendation_count: 0,
    score_threshold: 0.65,
    expansion_queries_json: null,
  });
  mockGetRun.mockResolvedValue({
    id: 555,
    status: "complete",
    recommendation_count: 2,
  });

  const onSubmitted = jest.fn();
  const user = userEvent.setup();
  render(<AiLitReviewLauncher onSubmitted={onSubmitted} />);

  const expSelect = await screen.findByLabelText(/experiment/i);
  await user.selectOptions(expSelect, "103");
  const button = await screen.findByRole("button", { name: /run/i });
  await user.click(button);

  await waitFor(() => {
    expect(mockRun).toHaveBeenCalledWith(103);
  });
});
