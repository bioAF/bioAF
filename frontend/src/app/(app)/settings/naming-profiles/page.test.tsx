import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import NamingProfilesPage from "./page";

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
}));

jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, loading: false }),
}));

jest.mock("@/components/shared/ContentLoading", () => ({ ContentLoading: () => null }));
jest.mock("@/components/naming/NamingProfileWizard", () => ({
  NamingProfileWizard: () => null,
}));
jest.mock("@/components/naming/NamingProfileDetail", () => ({
  NamingProfileDetail: () => null,
}));

jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), delete: jest.fn() },
}));

import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;
const mockDelete = api.delete as jest.Mock;

const profile = {
  id: 4,
  name: "Core facility convention",
  description: "How the core encodes filenames",
  delimiter: "_",
  status: "active",
  segments: [{ field_type: "string", identifier: "req", field_name: "requester" }],
};

beforeEach(() => {
  mockGet.mockReset();
  mockDelete.mockReset();
  mockGet.mockResolvedValue([profile]);
  mockDelete.mockResolvedValue({});
});

// Deactivating is irreversible: the API has no route back to `active` (only
// create/update/deactivate exist, and update never touches status), and the list
// only fetches active profiles, so the row disappears. A one-click irreversible
// action must ask first.
test("Deactivate asks before it deactivates", async () => {
  render(<NamingProfilesPage />);
  await waitFor(() => expect(screen.getByText("Core facility convention")).toBeInTheDocument());

  fireEvent.click(screen.getByRole("button", { name: "Deactivate" }));

  expect(mockDelete).not.toHaveBeenCalled();
  expect(screen.getByRole("dialog")).toBeInTheDocument();
  expect(
    screen.getByText(/Core facility convention/, { selector: "strong" }),
  ).toBeInTheDocument();
});

test("cancelling the confirmation leaves the profile active", async () => {
  render(<NamingProfilesPage />);
  await waitFor(() => expect(screen.getByText("Core facility convention")).toBeInTheDocument());

  fireEvent.click(screen.getByRole("button", { name: "Deactivate" }));
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

  expect(mockDelete).not.toHaveBeenCalled();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

test("confirming deactivates that profile", async () => {
  render(<NamingProfilesPage />);
  await waitFor(() => expect(screen.getByText("Core facility convention")).toBeInTheDocument());

  fireEvent.click(screen.getByRole("button", { name: "Deactivate" }));
  fireEvent.click(screen.getByRole("button", { name: "Deactivate profile" }));

  await waitFor(() => expect(mockDelete).toHaveBeenCalledWith("/api/naming-profiles/4"));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

// The dialog has to say what cannot be undone, not just "are you sure".
test("the confirmation states that deactivation cannot be undone in the app", async () => {
  render(<NamingProfilesPage />);
  await waitFor(() => expect(screen.getByText("Core facility convention")).toBeInTheDocument());

  fireEvent.click(screen.getByRole("button", { name: "Deactivate" }));

  expect(screen.getByRole("dialog")).toHaveTextContent(/cannot be reactivated/i);
});
