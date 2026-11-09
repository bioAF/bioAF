/**
 * Owner review 3: the first build-status read failing was discarded. Polling only started after a
 * successful "building" answer, and the out-of-date notice needed earlier data, so a 502 on the
 * first read left no notice, no Retry, and no further reads. A 403 is expected for roles without
 * infrastructure view (bench, viewer) and stays silent.
 */
import { render, screen, act, fireEvent } from "@/testing/renderWithProviders";

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
  usePathname: () => "/notebooks",
}));
jest.mock("@/lib/api", () => {
  const actual = jest.requireActual("@/lib/api");
  return { ApiError: actual.ApiError, api: { get: jest.fn(), post: jest.fn() } };
});
const mockComponents = {
  components: [{ key: "jupyterhub", enabled: true }, { key: "rstudio", enabled: true }],
  loading: false,
  failed: false,
  refetch: jest.fn(),
};
jest.mock("@/hooks/useComponents", () => ({ useComponents: () => mockComponents }));

import NotebooksPage from "./page";
import { api, ApiError } from "@/lib/api";

const mockGet = api.get as jest.Mock;
const BUILDING = { build_id: "abcdef123456", build_status: "WORKING", image_uri: null };
let buildReplies: Array<() => Promise<unknown>>;
let buildReads: number;

async function flush() {
  await act(async () => {
    for (let n = 0; n < 10; n++) await Promise.resolve();
  });
}
async function advance(ms: number) {
  await act(async () => {
    jest.advanceTimersByTime(ms);
  });
  await flush();
}

beforeEach(() => {
  jest.useFakeTimers();
  buildReplies = [];
  buildReads = 0;
  mockGet.mockReset();
  mockGet.mockImplementation((url: string) => {
    if (url.includes("build-status")) {
      buildReads += 1;
      const next = buildReplies.shift();
      return next ? next() : Promise.resolve(BUILDING);
    }
    if (url.includes("/notebooks/sessions")) return Promise.resolve({ sessions: [] });
    if (url.includes("experiments")) return Promise.resolve({ experiments: [] });
    if (url.includes("projects")) return Promise.resolve({ projects: [] });
    if (url.includes("/api/v1/environments")) return Promise.resolve({ environments: [], total: 0 });
    return Promise.resolve({});
  });
  jest.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});

const upstream = () => Promise.reject(new ApiError(502, "Request failed (HTTP 502)."));

test("a failed first build-status read is said, with Retry that recovers", async () => {
  buildReplies = [upstream];
  render(<NotebooksPage />);
  await flush();

  const notice = screen.getByTestId("build-status-load-failed");
  expect(notice).toHaveTextContent("Notebook image build status is unavailable. bioAF will keep checking.");

  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await flush();
  expect(screen.getByText("Notebook image is building")).toBeInTheDocument();
  expect(screen.queryByTestId("build-status-load-failed")).not.toBeInTheDocument();
});

test("after a failed first read bioAF keeps checking and clears the notice when a read succeeds", async () => {
  buildReplies = [upstream, upstream];
  render(<NotebooksPage />);
  await flush();
  expect(buildReads).toBe(1);

  await advance(15_000);
  expect(buildReads).toBe(2);
  expect(screen.getByTestId("build-status-load-failed")).toBeInTheDocument();

  await advance(15_000);
  expect(buildReads).toBe(3);
  expect(screen.queryByTestId("build-status-load-failed")).not.toBeInTheDocument();
  expect(screen.getByText("Notebook image is building")).toBeInTheDocument();
});

test("a 403 (no infrastructure view) is expected: no notice and no further reads", async () => {
  buildReplies = [() => Promise.reject(new ApiError(403, "Forbidden"))];
  render(<NotebooksPage />);
  await flush();

  expect(screen.queryByTestId("build-status-load-failed")).not.toBeInTheDocument();
  await advance(45_000);
  expect(buildReads).toBe(1);
});

test("a 403 after a failed first read clears the notice and stops re-checking", async () => {
  buildReplies = [upstream, () => Promise.reject(new ApiError(403, "Forbidden"))];
  render(<NotebooksPage />);
  await flush();
  expect(screen.getByTestId("build-status-load-failed")).toBeInTheDocument();

  await advance(15_000);
  expect(buildReads).toBe(2);
  expect(screen.queryByTestId("build-status-load-failed")).not.toBeInTheDocument();

  await advance(45_000);
  expect(buildReads).toBe(2);
});
