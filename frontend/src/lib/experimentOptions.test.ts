import { listAllExperiments } from "./experimentOptions";

jest.mock("@/lib/api", () => ({ api: { get: jest.fn() } }));
import { api } from "@/lib/api";
const mockGet = api.get as jest.Mock;

const experiments = (from: number, count: number) =>
  Array.from({ length: count }, (_, i) => ({ id: from + i, name: `Exp ${from + i}` }));

beforeEach(() => mockGet.mockReset());

test("reads every page", async () => {
  mockGet
    .mockResolvedValueOnce({ experiments: experiments(1, 200), total: 250 })
    .mockResolvedValueOnce({ experiments: experiments(201, 50), total: 250 });

  const all = await listAllExperiments();

  expect(all).toHaveLength(250);
  expect(mockGet).toHaveBeenNthCalledWith(1, "/api/experiments?page=1&page_size=200");
  expect(mockGet).toHaveBeenNthCalledWith(2, "/api/experiments?page=2&page_size=200");
});

test("stops at the total when the last page is full", async () => {
  mockGet.mockResolvedValueOnce({ experiments: experiments(1, 200), total: 200 });

  await listAllExperiments();

  expect(mockGet).toHaveBeenCalledTimes(1);
});

test("keeps the project narrowing on every page", async () => {
  mockGet.mockResolvedValueOnce({ experiments: experiments(1, 3), total: 3 });

  await listAllExperiments(7);

  expect(mockGet).toHaveBeenCalledWith("/api/experiments?project_id=7&page=1&page_size=200");
});

test("lists an experiment once if the pages shift underneath it", async () => {
  mockGet
    .mockResolvedValueOnce({ experiments: experiments(1, 200), total: 201 })
    .mockResolvedValueOnce({ experiments: experiments(200, 1), total: 201 });

  expect(await listAllExperiments()).toHaveLength(200);
});
