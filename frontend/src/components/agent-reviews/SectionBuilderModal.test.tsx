/**
 * Experiment review run selection: HTML reports can only be requested for runs the review
 * includes (the server refuses anything else). Deselecting a run clears its HTML report, a run's
 * HTML-report box is disabled while the run is excluded, the header toggle covers only included
 * runs, and both submit paths send HTML-report ids within the included runs.
 */
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { SectionBuilderModal } from "./SectionBuilderModal";
import { api } from "@/lib/api";

jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), post: jest.fn() } }));
jest.mock("@/components/shared/Modal", () => ({
  Modal: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
jest.mock("./AssembledPromptModal", () => ({
  AssembledPromptModal: ({ onSavedAndRun }: { onSavedAndRun: (s: unknown) => void }) => (
    <button
      onClick={() => onSavedAndRun({ id: 77, name: "Saved", body: "b", created_at: "2026-11-20" })}
    >
      Save and run
    </button>
  ),
}));

const runs = [101, 102].map((id) => ({
  id,
  pipeline_name: "rnaseq",
  pipeline_version: "1",
  status: "completed",
  created_at: "2026-11-20",
}));

const mockGet = api.get as jest.Mock;
const mockPost = api.post as jest.Mock;

beforeEach(() => {
  jest.clearAllMocks();
  mockGet.mockImplementation((url: string) => {
    if (url.endsWith("section_catalog"))
      return Promise.resolve({
        sections: [
          {
            id: "qc",
            label: "Quality",
            experiment_only: false,
            sub_items: [{ id: "qc.metric_review", label: "Metrics", default_on: true, prompt_fragment: "Review" }],
          },
        ],
        pipeline_run_defaults: ["qc.metric_review"],
        experiment_defaults: ["qc.metric_review"],
      });
    if (url.endsWith("prompts")) return Promise.resolve({ items: [] });
    return Promise.resolve({ runs });
  });
  mockPost.mockImplementation((url: string) =>
    url.endsWith("assemble_prompt")
      ? Promise.resolve({ body: "assembled" })
      : Promise.resolve({ job_id: 1, agent_review_id: 1 }),
  );
});

function renderModal(runId?: number) {
  render(
    <SectionBuilderModal
      entityType="experiment"
      experimentId={1}
      {...(runId !== undefined ? { runId } : {})}
      onCancel={jest.fn()}
      onSubmitted={jest.fn()}
      onError={jest.fn()}
    />,
  );
}

async function row(id: number) {
  const cell = await screen.findByText(new RegExp(`#${id} `));
  const [runBox, htmlBox] = within(cell.closest("tr")!).getAllByRole("checkbox");
  return { runBox, htmlBox };
}

function runBody() {
  const call = mockPost.mock.calls.find(([url]) => url === "/api/agent_reviews/run");
  expect(call).toBeDefined();
  return call![1];
}

test("deselecting a run clears its HTML report and disables that box", async () => {
  renderModal();
  await waitFor(() => expect(screen.getByRole("button", { name: "Run review" })).toBeEnabled());
  fireEvent.click(screen.getByRole("checkbox", { name: "Include HTML report for all runs" }));
  const r102 = await row(102);
  expect(r102.htmlBox).toBeChecked();

  fireEvent.click(r102.runBox);

  expect(r102.htmlBox).not.toBeChecked();
  expect(r102.htmlBox).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Run review" }));
  await waitFor(() => expect(onlyRunCalls()).toHaveLength(1));
  expect(runBody().included_run_ids).toEqual([101]);
  expect(runBody().include_html_report_run_ids).toEqual([101]);
});

test("the HTML report header toggle covers only included runs", async () => {
  renderModal(101);
  const r101 = await row(101);
  const r102 = await row(102);
  expect(r102.runBox).not.toBeChecked();
  expect(r102.htmlBox).toBeDisabled();

  const header = screen.getByRole("checkbox", { name: "Include HTML report for all runs" });
  fireEvent.click(header);

  expect(r101.htmlBox).toBeChecked();
  expect(r102.htmlBox).not.toBeChecked();
  expect(header).toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "Run review" }));
  await waitFor(() => expect(onlyRunCalls()).toHaveLength(1));
  expect(runBody().included_run_ids).toEqual([101]);
  expect(runBody().include_html_report_run_ids).toEqual([101]);
});

test("re-including a run enables its HTML report box without checking it", async () => {
  renderModal();
  await waitFor(() => expect(screen.getByRole("button", { name: "Run review" })).toBeEnabled());
  const r102 = await row(102);
  fireEvent.click(r102.runBox);
  expect(r102.htmlBox).toBeDisabled();
  fireEvent.click(r102.runBox);
  expect(r102.htmlBox).toBeEnabled();
  expect(r102.htmlBox).not.toBeChecked();
});

test("save and run sends HTML report ids within the included runs", async () => {
  renderModal();
  await waitFor(() => expect(screen.getByRole("button", { name: "Run review" })).toBeEnabled());
  fireEvent.click(screen.getByRole("checkbox", { name: "Include HTML report for all runs" }));
  fireEvent.click((await row(102)).runBox);

  fireEvent.click(screen.getByRole("button", { name: "Display prompt" }));
  fireEvent.click(await screen.findByRole("button", { name: "Save and run" }));

  await waitFor(() => expect(onlyRunCalls()).toHaveLength(1));
  expect(runBody().custom_prompt_id).toBe(77);
  expect(runBody().included_run_ids).toEqual([101]);
  expect(runBody().include_html_report_run_ids).toEqual([101]);
});

function onlyRunCalls() {
  return mockPost.mock.calls.filter(([url]) => url === "/api/agent_reviews/run");
}

test("clicking the HTML report header text toggles HTML reports, not run inclusion", async () => {
  renderModal();
  await waitFor(() => expect(screen.getByRole("button", { name: "Run review" })).toBeEnabled());
  const r101 = await row(101);
  const r102 = await row(102);
  expect(r101.runBox).toBeChecked();

  fireEvent.click(screen.getByText("HTML report"));

  expect(r101.runBox).toBeChecked();
  expect(r101.htmlBox).toBeChecked();
  expect(r102.htmlBox).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Include HTML report for all runs" })).toBeChecked();
});

test("run checkboxes do not share an element id", async () => {
  renderModal();
  await row(102);
  expect(document.querySelectorAll("#html-report")).toHaveLength(0);
});
