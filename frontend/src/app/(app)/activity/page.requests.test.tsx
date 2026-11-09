/**
 * The activity feed accepted answers in arrival order, so a slow answer for an earlier filter or
 * page could replace the current one (plan 13). The feed is keyed by page and every filter, and
 * only the current key's answer is shown.
 */
import { render, screen, fireEvent, act } from "@testing-library/react";

jest.mock("next/navigation", () => ({ usePathname: () => "/activity", useRouter: () => ({ push: jest.fn() }) }));
jest.mock("@/components/layout/Breadcrumb", () => ({ Breadcrumb: () => null }));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn() } }));

import ActivityFeedPage from "./page";
import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;
type Pending = { url: string; resolve: (v: unknown) => void; reject: (e: unknown) => void };
let pending: Pending[];

function event(id: number, summary: string) {
  return {
    id, user_id: 1, user_email: "a@lab.org", event_type: "data.uploaded", entity_type: null,
    entity_id: null, summary, severity: "info", created_at: "2026-03-10T12:00:00Z",
  };
}

/** The newest pending request for `fragment`: the one the page currently owns. */
function take(fragment: string): Pending {
  const index = pending.map((p) => p.url.includes(fragment)).lastIndexOf(true);
  if (index < 0) throw new Error(`no pending request matching ${fragment}: ${pending.map((p) => p.url)}`);
  return pending.splice(index, 1)[0];
}

function answer(fragment: string, summaries: string[], total = summaries.length) {
  const p = take(fragment);
  return act(async () => {
    p.resolve({ events: summaries.map((s, i) => event(i + 1, s)), total });
  });
}

function fail(fragment: string) {
  const p = take(fragment);
  return act(async () => {
    p.reject(new Error("502"));
  });
}

beforeEach(() => {
  pending = [];
  mockGet.mockReset();
  mockGet.mockImplementation(
    (url: string) => new Promise((resolve, reject) => pending.push({ url, resolve, reject })),
  );
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => jest.restoreAllMocks());

test("an earlier user filter's late answer does not replace the current one", async () => {
  render(<ActivityFeedPage />);
  await answer("/api/activity-feed?", ["everything"]);

  fireEvent.change(screen.getByTestId("filter-user"), { target: { value: "m" } });
  fireEvent.change(screen.getByTestId("filter-user"), { target: { value: "maria" } });
  await answer("user_email=maria", ["maria's upload"]);
  await answer("user_email=m", ["someone else's upload"]);

  expect(screen.getByText("maria's upload")).toBeInTheDocument();
  expect(screen.queryByText("someone else's upload")).not.toBeInTheDocument();
});

test("a new event type does not present the previous events as current", async () => {
  render(<ActivityFeedPage />);
  await answer("/api/activity-feed?", ["an upload"]);

  fireEvent.change(screen.getByTestId("filter-event-type"), { target: { value: "pipeline.completed" } });

  expect(screen.queryByText("an upload")).not.toBeInTheDocument();
});

test("an initial failure is an error with retry, not an empty feed", async () => {
  render(<ActivityFeedPage />);
  await fail("/api/activity-feed?");
  expect(screen.getByTestId("error-message")).toHaveTextContent(/activity could not be loaded/i);

  fireEvent.click(screen.getByTestId("error-retry"));
  await answer("/api/activity-feed?", ["an upload"]);
  expect(screen.getByText("an upload")).toBeInTheDocument();
});
