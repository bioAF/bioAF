import { loadProjectOptions } from "./projectOptions";

jest.mock("@/lib/api", () => ({ api: { get: jest.fn() } }));

import { api } from "@/lib/api";
const mockGet = api.get as jest.Mock;

beforeEach(() => {
  mockGet.mockReset();
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => jest.restoreAllMocks());

test("returns the projects it loaded", async () => {
  mockGet.mockResolvedValue({ projects: [{ id: 1, name: "Atlas" }], total: 1 });
  await expect(loadProjectOptions()).resolves.toEqual({
    status: "loaded",
    projects: [{ id: 1, name: "Atlas" }],
  });
});

test("a 403 is a denial, and is not logged as a fault", async () => {
  mockGet.mockRejectedValue(Object.assign(new Error("role_missing"), { status: 403 }));
  await expect(loadProjectOptions()).resolves.toEqual({ status: "denied", projects: [] });
  expect(console.error).not.toHaveBeenCalled();
});

test.each([
  ["a 500", Object.assign(new Error("boom"), { status: 500 })],
  ["a network error", new TypeError("Failed to fetch")],
])("%s is a failure, and is logged", async (_label, error) => {
  mockGet.mockRejectedValue(error);
  await expect(loadProjectOptions()).resolves.toEqual({ status: "failed", projects: [] });
  expect(console.error).toHaveBeenCalled();
});
