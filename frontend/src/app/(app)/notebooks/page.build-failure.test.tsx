/**
 * Owner review (same class as the reference-import and glossary-scan polls): a failed image-build
 * status read left "Notebook image is building" on screen as if current, with no warning.
 */
import { render, screen, act } from "@/testing/renderWithProviders";

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
  usePathname: () => "/notebooks",
}));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), post: jest.fn() } }));
const mockComponents = {
  components: [{ key: "jupyterhub", enabled: true }, { key: "rstudio", enabled: true }],
  loading: false,
  failed: false,
  refetch: jest.fn(),
};
jest.mock("@/hooks/useComponents", () => ({ useComponents: () => mockComponents }));

import NotebooksPage from "./page";
import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;
const BUILDING = { build_id: "abcdef123456", build_status: "WORKING", image_uri: null };
let buildReplies: Array<() => Promise<unknown>>;

async function flush() {
  await act(async () => {
    for (let n = 0; n < 10; n++) await Promise.resolve();
  });
}

beforeEach(() => {
  jest.useFakeTimers();
  buildReplies = [];
  mockGet.mockReset();
  mockGet.mockImplementation((url: string) => {
    if (url.includes("build-status")) {
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

test("a failed build-status read keeps the banner but says the status may be out of date", async () => {
  buildReplies = [() => Promise.resolve(BUILDING), () => Promise.reject(new Error("502"))];
  render(<NotebooksPage />);
  await flush();
  expect(screen.getByText("Notebook image is building")).toBeInTheDocument();

  await act(async () => {
    jest.advanceTimersByTime(15_000);
  });
  await flush();
  expect(screen.getByTestId("build-status-unavailable")).toHaveTextContent(/could not be read/i);

  await act(async () => {
    jest.advanceTimersByTime(15_000);
  });
  await flush();
  expect(screen.queryByTestId("build-status-unavailable")).not.toBeInTheDocument();
});
