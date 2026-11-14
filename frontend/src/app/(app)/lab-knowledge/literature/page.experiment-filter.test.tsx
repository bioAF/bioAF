/**
 * The Library experiment filter reaches every experiment, a page at a time, and narrows by the chosen
 * project on the server (#104).
 */
import { render, screen, waitFor } from "@/testing/renderWithProviders";
import LiteraturePage from "./page";
import { literature } from "@/lib/literature";
import { api } from "@/lib/api";

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
  usePathname: () => "/lab-knowledge/literature",
}));
jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ role_name: "custom" }),
}));
jest.mock("@/components/literature/AssociatePaperModal", () => ({ AssociatePaperModal: () => null }));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn() } }));
jest.mock("@/lib/literature", () => {
  const actual = jest.requireActual("@/lib/literature");
  return { ...actual, literature: { listPapers: jest.fn(), bulkDismiss: jest.fn() } };
});
import userEvent from "@testing-library/user-event";

const mockGet = api.get as jest.Mock;
const many = Array.from({ length: 250 }, (_, i) => ({ id: 1 + i, name: `Lib ${i + 1}`, project: { id: (i % 2) + 1, name: "P" } }));

beforeEach(() => {
  mockGet.mockReset();
  (literature.listPapers as jest.Mock).mockResolvedValue({ items: [], total: 0 });
  mockGet.mockImplementation((url: string) => {
    if (url.startsWith("/api/projects")) return Promise.resolve({ projects: [{ id: 1, name: "One" }, { id: 2, name: "Two" }] });
    const p = new URL(url, "http://x").searchParams;
    const matching = many.filter(
      (e) => e.name.includes(p.get("name") ?? "") && (!p.get("project_id") || String(e.project.id) === p.get("project_id")),
    );
    const page = Number(p.get("page"));
    return Promise.resolve({ experiments: matching.slice((page - 1) * 100, page * 100), total: matching.length });
  });
});

test("an experiment past the first hundred filters the Library", async () => {
  const user = userEvent.setup();
  render(<LiteraturePage />);
  await user.type(await screen.findByRole("searchbox", { name: "Search names" }), "Lib 237");
  await user.selectOptions(screen.getByLabelText("Experiment"), await screen.findByRole("option", { name: "Lib 237" }));
  await waitFor(() =>
    expect((literature.listPapers as jest.Mock).mock.calls.at(-1)?.[0]).toMatchObject({ experiment_id: 237 }),
  );
});

test("the project filter narrows the experiment choices on the server", async () => {
  const user = userEvent.setup();
  render(<LiteraturePage />);
  await user.selectOptions(await screen.findByLabelText("Project"), await screen.findByRole("option", { name: "Two" }));
  await waitFor(() => expect(mockGet.mock.calls.some(([u]) => String(u).includes("project_id=2"))).toBe(true));
});
