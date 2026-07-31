import { render, screen } from "@testing-library/react";
import LiteratureRecommendationsPage from "./page";
import { literature } from "@/lib/literature";

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
  usePathname: () => "/lab-knowledge/literature/recommendations",
}));
jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ role_name: "admin" }),
}));
jest.mock("@/components/literature/AiLitReviewLauncher", () => ({ AiLitReviewLauncher: () => null }));
jest.mock("@/lib/literature", () => {
  const actual = jest.requireActual("@/lib/literature");
  return {
    ...actual,
    literature: { listRecommendations: jest.fn(), dismissRecommendation: jest.fn() },
  };
});

const mockList = literature.listRecommendations as jest.Mock;

test("renders a breadcrumb back to the Literature library", async () => {
  mockList.mockResolvedValue({ items: [] });
  render(<LiteratureRecommendationsPage />);
  const breadcrumb = await screen.findByTestId("breadcrumb");
  expect(breadcrumb).toHaveTextContent("Literature");
  expect(screen.getByTestId("breadcrumb-current")).toHaveTextContent("AI Literature Review");
});

test("shows a retry-able error when recommendations fail to load", async () => {
  mockList.mockRejectedValue(new Error("boom"));
  render(<LiteratureRecommendationsPage />);

  expect(await screen.findByTestId("error-state")).toBeInTheDocument();
  expect(screen.getByText(/couldn't load recommendations/i)).toBeInTheDocument();
  expect(screen.getByTestId("error-retry")).toBeInTheDocument();
});
