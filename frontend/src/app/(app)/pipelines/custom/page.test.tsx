import { render, screen } from "@testing-library/react";
import { NOT_SET } from "@/lib/placeholders";
import CustomPipelineListPage from "./page";

const mockGet = jest.fn();
jest.mock("@/lib/api", () => ({ api: { get: (p: string) => mockGet(p) } }));
const router = { push: jest.fn() };
jest.mock("next/navigation", () => ({ useRouter: () => router }));
jest.mock("@/hooks/usePermissions", () => ({ usePermissions: () => ({ canAccess: () => true, loading: false }) }));

function pipeline(id: number, name: string, created_at: string | null) {
  return { id, name, description: null, pipeline_key: `p${id}`, created_by_user_id: 1, created_at, updated_at: created_at };
}

beforeEach(() => {
  mockGet.mockReset();
  mockGet.mockImplementation((path: string) => {
    if (path === "/api/v1/custom-pipelines") {
      return Promise.resolve([pipeline(1, "Dated Pipeline", "2026-03-04T05:06:07Z"), pipeline(2, "Legacy Pipeline", null)]);
    }
    if (path === "/api/pipelines") return Promise.resolve({ pipelines: [] });
    return Promise.resolve({ runs: [] });
  });
});

it("shows the not-set placeholder for a pipeline whose creation time was never recorded", async () => {
  render(<CustomPipelineListPage />);
  const dated = (await screen.findByText("Dated Pipeline")).closest("tr")!;
  const legacyRow = screen.getByText("Legacy Pipeline").closest("tr")!;
  expect(legacyRow).toHaveTextContent(NOT_SET);
  expect(legacyRow).not.toHaveTextContent("1970");

  expect(dated).toHaveTextContent(new Date("2026-03-04T05:06:07Z").toLocaleString());
});
