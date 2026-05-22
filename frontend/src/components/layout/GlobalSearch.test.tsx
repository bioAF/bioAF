import { render, screen, fireEvent, waitFor } from "@testing-library/react";

const mockPush = jest.fn();
jest.mock("next/navigation", () => ({ useRouter: () => ({ push: mockPush }) }));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn() } }));

import { GlobalSearch } from "./GlobalSearch";
import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;

beforeEach(() => {
  mockGet.mockReset();
  mockPush.mockReset();
});

test("searches by name after the user pauses and shows results", async () => {
  mockGet.mockResolvedValue({
    results: [{ entity_type: "experiment", entity_id: 5, name: "Alpha Study", experiment_id: 5 }],
  });
  render(<GlobalSearch debounceMs={10} />);

  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "alpha" } });

  await waitFor(() => expect(mockGet).toHaveBeenCalledWith("/api/search/quick?q=alpha"));
  expect(await screen.findByText("Alpha Study")).toBeInTheDocument();
});

test("clicking a result navigates to it and clears the box", async () => {
  mockGet.mockResolvedValue({
    results: [{ entity_type: "file", entity_id: 77, name: "reads.fastq.gz" }],
  });
  render(<GlobalSearch debounceMs={10} />);
  const input = screen.getByRole("searchbox") as HTMLInputElement;

  fireEvent.change(input, { target: { value: "reads" } });
  fireEvent.click(await screen.findByText("reads.fastq.gz"));

  expect(mockPush).toHaveBeenCalledWith("/data/files?file=77");
  expect(input.value).toBe("");
});

test("does not search for a blank query", async () => {
  render(<GlobalSearch debounceMs={10} />);
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "   " } });
  await new Promise((r) => setTimeout(r, 40));
  expect(mockGet).not.toHaveBeenCalled();
});

test("shows a no-matches message when nothing is found", async () => {
  mockGet.mockResolvedValue({ results: [] });
  render(<GlobalSearch debounceMs={10} />);
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "zzz" } });
  expect(await screen.findByText(/no matches/i)).toBeInTheDocument();
});
