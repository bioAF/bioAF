/**
 * The run detail page renders pipeline report content that a pipeline wrote.
 * Markdown reports go through the real Markdown renderer (not the global
 * passthrough mock) so these assertions prove the parser's behavior; HTML
 * reports render in a frame that cannot reach the app's origin.
 */
import { render, screen, fireEvent, waitFor } from "@/testing/renderWithProviders";

jest.unmock("react-markdown");

const mockUseParams = jest.fn();
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
  useParams: () => mockUseParams(),
  usePathname: () => "/pipelines/runs/42",
}));

jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ email: "test@bioaf.org", role: "admin", sub: "1" }),
  getToken: () => "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIiwicm9sZSI6ImFkbWluIn0.fake",
}));

jest.mock("@/hooks/useComponents", () => ({
  useComponents: () => ({ components: [], loading: false, refetch: jest.fn() }),
}));

jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, roleName: "admin", loading: false, permissions: new Set() }),
}));

const mockApiGet = jest.fn();
jest.mock("@/lib/api", () => ({
  api: {
    get: (...args: unknown[]) => mockApiGet(...args),
    post: jest.fn(),
  },
  ApiError: class ApiError extends Error {},
  fetchText: jest.requireActual("@/lib/api").fetchText,
}));

const originalFetch = global.fetch;

afterEach(() => {
  global.fetch = originalFetch;
});

const baseRun = {
  id: 42,
  pipeline_key: "custom",
  pipeline_name: "Custom Pipeline",
  pipeline_version: "1",
  experiment: { id: 1, name: "Test Experiment" },
  submitted_by: { id: 1, name: "Admin", email: "admin@test.com" },
  status: "completed" as const,
  parameters: {},
  input_files: null,
  output_files: null,
  progress: null,
  cost_estimate: null,
  error_message: null,
  work_dir: null,
  slurm_job_id: null,
  compute_job_ref: "bioaf-pipeline-42",
  provider_metadata: null,
  actual_cost: null,
  reference_genome: null,
  alignment_algorithm: null,
  resume_from_run_id: null,
  review_verdict: null,
  started_at: "2026-03-11T10:00:00Z",
  completed_at: "2026-03-11T11:00:00Z",
  created_at: "2026-03-11T10:00:00Z",
  processes: [],
  samples: [],
};

function customRun(reportFormat: string | null) {
  return {
    ...baseRun,
    custom_pipeline_version_id: 9,
    output_files: {
      report_path: "gs://results/r/report/report." + (reportFormat ?? "html"),
      ...(reportFormat ? { report_format: reportFormat } : {}),
    },
  };
}

async function openReport(run: object, body: string) {
  mockUseParams.mockReturnValue({ id: "42" });
  mockApiGet.mockImplementation((url: string) => {
    if (url.includes("/references")) return Promise.resolve([]);
    if (url.includes("/overview")) return Promise.reject(new Error("not needed"));
    return Promise.resolve(run);
  });
  global.fetch = jest.fn().mockResolvedValue({
    ok: true,
    status: 200,
    headers: new Headers(),
    text: () => Promise.resolve(body),
    json: () => Promise.resolve({}),
  });
  const Page = require("@/app/(app)/pipelines/runs/[id]/page").default;
  const view = render(<Page />);
  await waitFor(() => expect(screen.getByRole("button", { name: "Report" })).toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: "Report" }));
  return view;
}

async function renderMarkdownReport(markdown: string) {
  const view = await openReport(customRun("md"), markdown);
  await waitFor(() => expect(view.container.querySelector("[data-testid='markdown-report']")).not.toBeNull());
  return view.container.querySelector("[data-testid='markdown-report']") as HTMLElement;
}

function hasEventHandlerAttribute(root: HTMLElement): boolean {
  return Array.from(root.querySelectorAll("*")).some((el) =>
    Array.from(el.attributes).some((a) => a.name.toLowerCase().startsWith("on")),
  );
}

describe("Markdown report rendering", () => {
  test("keeps headings, lists, emphasis, inline code and fenced code", async () => {
    const report = await renderMarkdownReport(
      "# Summary\n\nAll **12** samples passed *QC*.\n\n- one\n- two\n\nUse `samtools`.\n\n```\n<b>literal</b>\n```\n",
    );
    expect(report.querySelector("h1")).toHaveTextContent("Summary");
    expect(report.querySelector("strong")).toHaveTextContent("12");
    expect(report.querySelector("em")).toHaveTextContent("QC");
    expect(report.querySelectorAll("li")).toHaveLength(2);
    expect(report.querySelector("p code")).toHaveTextContent("samtools");
    expect(report.querySelector("pre code")).toHaveTextContent("<b>literal</b>");
    expect(report.querySelector("b")).toBeNull();
  });

  test("a quote in a link target cannot add an event handler", async () => {
    const report = await renderMarkdownReport(
      '[click](https://example.org/" onmouseover="alert(1))\n\n[x](https://example.org/"onclick="alert(1)")',
    );
    expect(hasEventHandlerAttribute(report)).toBe(false);
  });

  test("raw HTML in the report is not rendered", async () => {
    const report = await renderMarkdownReport(
      'Before\n\n<img src=x onerror="alert(1)">\n\n<script>alert(1)</script>\n\n<a href="https://evil.example">Sign in</a>\n\nAfter <b onclick="alert(1)">bold</b>',
    );
    expect(report.querySelector("img, script, b")).toBeNull();
    expect(report.querySelector("a")).toBeNull();
    expect(hasEventHandlerAttribute(report)).toBe(false);
    expect(report).toHaveTextContent("Before");
    expect(report).toHaveTextContent("After");
  });

  test.each([
    ["javascript:alert(1)"],
    ["JaVaScRiPt:alert(1)"],
    ["&#106;avascript:alert(1)"],
    ["java&#x09;script:alert(1)"],
    ["%6Aavascript:alert(1)"],
    ["data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg=="],
    ["vbscript:msgbox(1)"],
    ["xmpp:someone@example.org"],
    ["irc://irc.example.org/chan"],
  ])("rejects the link target %s", async (target) => {
    const report = await renderMarkdownReport(`[open](${target})`);
    expect(report).toHaveTextContent("open");
    for (const a of Array.from(report.querySelectorAll("a"))) {
      const href = a.getAttribute("href");
      expect(href === null || href === "").toBe(true);
    }
  });

  test.each([
    ["https://example.org/report", "_blank"],
    ["http://example.org/report", "_blank"],
    ["mailto:lab@example.org", "_blank"],
    ["results/table.csv", "_blank"],
    ["#tasks", null],
  ])("keeps the safe link target %s", async (target, expectedTarget) => {
    const report = await renderMarkdownReport(`[open](${target})`);
    const link = report.querySelector("a");
    expect(link).not.toBeNull();
    expect(link!.getAttribute("href")).toBe(target);
    expect(link!.getAttribute("target")).toBe(expectedTarget);
    if (expectedTarget) expect(link!.getAttribute("rel")).toContain("noopener");
  });

  test("images do not load: they show their alt text", async () => {
    const report = await renderMarkdownReport("![Tracker pixel](https://tracker.example/i.png)");
    expect(report.querySelector("img")).toBeNull();
    expect(report).toHaveTextContent("Tracker pixel");
  });
});

describe("HTML report isolation", () => {
  test.each([
    ["Nextflow", { ...baseRun }],
    ["custom HTML", customRun("html")],
    ["custom report without a recorded format", customRun(null)],
  ])("the %s report runs scripts without the app's origin", async (_label, run) => {
    const { container } = await openReport(run, "<html><body><script>parent.document</script>Report</body></html>");
    let iframe: HTMLIFrameElement | null = null;
    await waitFor(() => {
      iframe = container.querySelector("iframe");
      expect(iframe).not.toBeNull();
    });
    const tokens = (iframe!.getAttribute("sandbox") ?? "").split(/\s+/);
    expect(iframe!.hasAttribute("sandbox")).toBe(true);
    expect(tokens).toContain("allow-scripts");
    expect(tokens).not.toContain("allow-same-origin");
    expect(tokens).not.toContain("allow-top-navigation");
    expect(container.querySelector("[data-testid='markdown-report']")).toBeNull();
  });
});
