import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import LiteratureSourcesPage from "./page";
import { literature } from "@/lib/literature";

jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ role_name: "admin" }),
}));
jest.mock("@/lib/literature", () => ({
  literature: {
    listSources: jest.fn(),
    updateSource: jest.fn(),
    testSource: jest.fn(),
  },
}));

const mockList = literature.listSources as jest.Mock;
const mockUpdate = literature.updateSource as jest.Mock;

beforeEach(() => {
  mockList.mockResolvedValue({
    items: [{ source: "pubmed", enabled: true, has_api_key: false, rate_limit_override: null }],
  });
  mockUpdate.mockResolvedValue({});
});

test("shows a retry-able error when sources fail to load", async () => {
  mockList.mockRejectedValue(new Error("boom"));
  render(<LiteratureSourcesPage />);
  expect(await screen.findByTestId("error-state")).toBeInTheDocument();
  expect(screen.getByText(/couldn't load literature sources/i)).toBeInTheDocument();
  expect(screen.getByTestId("error-retry")).toBeInTheDocument();
});

test("collects the API key in a styled password dialog, never a native prompt()", async () => {
  const promptSpy = jest.spyOn(window, "prompt");
  render(<LiteratureSourcesPage />);
  await screen.findByText(/PubMed/);

  fireEvent.click(screen.getByRole("button", { name: /set key/i }));

  const field = screen.getByLabelText("API key");
  expect(field).toHaveAttribute("type", "password");
  expect(promptSpy).not.toHaveBeenCalled();

  fireEvent.change(field, { target: { value: "sk-abc" } });
  fireEvent.click(screen.getByRole("button", { name: /save key/i }));

  await waitFor(() =>
    expect(mockUpdate).toHaveBeenCalledWith("pubmed", { api_key: "sk-abc" }),
  );
  promptSpy.mockRestore();
});
