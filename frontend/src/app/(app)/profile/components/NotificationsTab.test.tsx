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

test("each toggle names its channel and event, so the right switch is unambiguous", async () => {
  mockGet.mockResolvedValue([]);
  render(<NotificationsTab />);
  await screen.findByText("Review reminder");

  expect(screen.getByRole("button", { name: "In-App notifications for Review reminder" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Email notifications for Review reminder" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Slack notifications for Review reminder" })).toBeInTheDocument();
});

test("channel header labels sit in the same column width as the toggles they control", async () => {
  // The header labels used to be text-width (In-App/Email/Slack) while the toggles below sat in
  // fixed w-12 cells, so each label drifted left of its own column - up to most of a column pitch.
  // jsdom cannot see the misalignment, so pin the shared column width instead.
  mockGet.mockResolvedValue([]);
  render(<NotificationsTab />);
  await screen.findByText("Review reminder");

  const header = screen.getByTitle("Toggle all Email for Pipelines & Analysis");
  const toggle = screen.getByRole("button", { name: "Email notifications for Review reminder" });
  expect(header.className).toContain("w-12");
  expect(toggle.parentElement?.className).toContain("w-12");
});

test("defaults match the server: in-app on, email off when nothing is stored", async () => {
  mockGet.mockResolvedValue([]);
  render(<NotificationsTab />);
  await screen.findByText("Review reminder");

  // The backend delivers in-app by default and treats email as opt-in
  // (notification_router.DEFAULT_ON_CHANNELS); the toggles must show the same thing.
  expect(
    screen.getByRole("button", { name: "In-App notifications for Review reminder" }),
  ).toHaveAttribute("aria-pressed", "true");
  expect(
    screen.getByRole("button", { name: "Email notifications for Review reminder" }),
  ).toHaveAttribute("aria-pressed", "false");
});

test("turning email on for one event saves an explicit opt-in row", async () => {
  mockGet.mockResolvedValue([]);
  mockPut.mockResolvedValue({});
  render(<NotificationsTab />);
  await screen.findByText("Review reminder");

  fireEvent.click(screen.getByRole("button", { name: "Email notifications for Review reminder" }));
  fireEvent.click(screen.getByRole("button", { name: /save preferences/i }));

  await waitFor(() => expect(mockPut).toHaveBeenCalled());
  const body = mockPut.mock.calls[0][1] as { preferences: { event_type: string; channel: string; enabled: boolean }[] };
  expect(body.preferences).toContainEqual({
    event_type: "pipeline_run.review_reminder",
    channel: "email",
    enabled: true,
  });
});

test("a failed load is surfaced and blocks saving over the stored settings", async () => {
  // The load used to be swallowed, so a failure rendered every toggle at its channel default and a
  // Save from that state wrote those defaults over whatever the user actually had stored.
  mockGet.mockRejectedValue(new Error("boom"));
  render(<NotificationsTab />);

  expect(await screen.findByText(/could not load your notification preferences/i)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /save preferences/i })).toBeDisabled();
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
