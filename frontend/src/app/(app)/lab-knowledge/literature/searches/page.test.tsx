import { render, screen } from "@testing-library/react";
import LiteratureSearchesPage from "./page";
import { literature } from "@/lib/literature";

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
  usePathname: () => "/lab-knowledge/literature/searches",
}));
jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ role_name: "admin" }),
}));
jest.mock("@/lib/literature", () => {
  const actual = jest.requireActual("@/lib/literature");
  return { ...actual, literature: { listSearches: jest.fn() } };
});

const mockListSearches = literature.listSearches as jest.Mock;

test("renders a breadcrumb back to the Literature library", async () => {
  mockListSearches.mockResolvedValue({ items: [] });
  render(<LiteratureSearchesPage />);
  const breadcrumb = await screen.findByTestId("breadcrumb");
  expect(breadcrumb).toHaveTextContent("Literature");
  expect(screen.getByTestId("breadcrumb-current")).toHaveTextContent("Searches");
});

test("shows a retry-able error when searches fail to load", async () => {
  mockListSearches.mockRejectedValue(new Error("boom"));
  render(<LiteratureSearchesPage />);

  expect(await screen.findByTestId("error-state")).toBeInTheDocument();
  expect(screen.getByText(/couldn't load searches/i)).toBeInTheDocument();
  expect(screen.getByTestId("error-retry")).toBeInTheDocument();
});
