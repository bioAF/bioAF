import { render, screen, fireEvent, waitFor } from "@testing-library/react";

const mockGet = jest.fn();
const mockPut = jest.fn();
jest.mock("@/lib/api", () => ({
  api: { get: (...a: unknown[]) => mockGet(...a), put: (...a: unknown[]) => mockPut(...a) },
}));
jest.mock("@/components/shared/ContentLoading", () => ({ ContentLoading: () => <div /> }));

import { NotificationsTab } from "./NotificationsTab";

beforeEach(() => {
  mockGet.mockReset();
  mockPut.mockReset();
});

test("loads preferences and renders event categories", async () => {
  mockGet.mockResolvedValue([]);
  render(<NotificationsTab />);

  await waitFor(() => expect(mockGet).toHaveBeenCalledWith("/api/notifications/preferences"));
  expect(await screen.findByText("Pipeline completed")).toBeInTheDocument();
});

test("saves the current preferences", async () => {
  mockGet.mockResolvedValue([]);
  mockPut.mockResolvedValue({});
  render(<NotificationsTab />);
  await screen.findByText("Pipeline completed");

  fireEvent.click(screen.getByRole("button", { name: /save preferences/i }));
  await waitFor(() => expect(mockPut).toHaveBeenCalledWith("/api/notifications/preferences", expect.anything()));
  expect(await screen.findByText(/preferences saved/i)).toBeInTheDocument();
});
