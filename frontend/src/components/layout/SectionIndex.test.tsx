import { render, screen, waitFor } from "@testing-library/react";

const mockReplace = jest.fn();
jest.mock("next/navigation", () => ({ useRouter: () => ({ replace: mockReplace }) }));

const mockNav = jest.fn();
jest.mock("@/hooks/useVisibleNavSections", () => ({ useVisibleNavSections: () => mockNav() }));

import { SectionIndex } from "./SectionIndex";

beforeEach(() => {
  mockReplace.mockReset();
  mockNav.mockReturnValue({
    sections: [],
    loading: false,
    firstChildPath: (label: string) => (label === "Settings" ? "/settings/users" : null),
  });
});

test("a bare section URL lands on the first page it holds", async () => {
  render(<SectionIndex section="Settings" />);
  await waitFor(() => expect(mockReplace).toHaveBeenCalledWith("/settings/users"));
});

test("it replaces rather than pushes, so Back does not bounce off it", async () => {
  render(<SectionIndex section="Settings" />);
  await waitFor(() => expect(mockReplace).toHaveBeenCalled());
  // Only replace exists on the mocked router: a push here would throw.
  expect(mockReplace).toHaveBeenCalledTimes(1);
});

test("it waits for permissions instead of guessing while they load", () => {
  mockNav.mockReturnValue({ sections: [], loading: true, firstChildPath: () => null });
  render(<SectionIndex section="Settings" />);
  expect(mockReplace).not.toHaveBeenCalled();
});

test("a section that holds nothing for this user says so instead of redirecting", async () => {
  render(<SectionIndex section="Infrastructure" />);

  expect(await screen.findByText(/nothing in Infrastructure/i)).toBeInTheDocument();
  expect(mockReplace).not.toHaveBeenCalled();
});

test("that message points somewhere the user can definitely go", async () => {
  render(<SectionIndex section="Infrastructure" />);
  const link = await screen.findByRole("link", { name: /dashboard/i });
  expect(link).toHaveAttribute("href", "/dashboard");
});
