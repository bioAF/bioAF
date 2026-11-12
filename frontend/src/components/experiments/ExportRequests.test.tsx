/**
 * The experiment, project, and GEO export modals request through the shared application client:
 * the configured API base, the session credential, the shared status policy, the
 * server's attachment filename with the old name as fallback, and the backend's error text.
 */
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { fakeServer } from "@/testing/fakeServer";

const mockNavigateToLogin = jest.fn();
jest.mock("@/lib/navigation", () => ({ navigateToLogin: () => mockNavigateToLogin() }));

import { DataExportModal } from "./DataExportModal";
import { GeoExportModal } from "./GeoExportModal";
import { ProjectExportModal } from "@/components/projects/ProjectExportModal";
import { getToken } from "@/lib/auth";
import { startSession } from "@/lib/session";
import { capabilitiesCache, MINIMAL_CAPABILITIES } from "@/hooks/capabilitiesCache";

const realFetch = global.fetch;
const realEnv = process.env.NEXT_PUBLIC_API_URL;
let saved: string[];

beforeEach(() => {
  process.env.NEXT_PUBLIC_API_URL = "https://api.lab.org";
  localStorage.clear();
  startSession("live.jwt");
  saved = [];
  (URL as unknown as { createObjectURL: unknown }).createObjectURL = jest.fn(() => "blob:x");
  (URL as unknown as { revokeObjectURL: unknown }).revokeObjectURL = jest.fn();
  jest.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
    saved.push(this.download);
  });
  jest.spyOn(console, "error").mockImplementation(() => {});
  mockNavigateToLogin.mockReset();
});

afterEach(() => {
  global.fetch = realFetch;
  if (realEnv === undefined) delete process.env.NEXT_PUBLIC_API_URL;
  else process.env.NEXT_PUBLIC_API_URL = realEnv;
  jest.restoreAllMocks();
});

const ESTIMATE = { total_bytes: 2048, breakdown: { results: 2048 }, experiments: [] };

const MODALS = [
  {
    name: "experiment",
    base: "/api/experiments/4/export",
    fallback: "experiment_4_export.zip",
    render: () =>
      render(<DataExportModal experimentId={4} experimentName="E" isOpen onClose={() => {}} />),
  },
  {
    name: "project",
    base: "/api/projects/4/export",
    fallback: "project_4_export.zip",
    render: () =>
      render(<ProjectExportModal projectId={4} projectName="P" isOpen onClose={() => {}} />),
  },
];

describe.each(MODALS)("the $name export modal", ({ base, fallback, render: open }) => {
  async function toSizeStep() {
    open();
    fireEvent.click(screen.getByRole("button", { name: "Calculate Size" }));
    await screen.findByRole("button", { name: "Download Now" });
  }

  test("estimates with the session credential at the configured API", async () => {
    const { requests } = fakeServer([[`${base}/estimate`, { json: ESTIMATE }]]);
    await toSizeStep();
    expect(requests[0].url).toBe(`https://api.lab.org${base}/estimate?include_fastq=false`);
    expect(requests[0].headers.Authorization).toBe("Bearer live.jwt");
  });

  test("a failed estimate shows the backend's explanation", async () => {
    fakeServer([[`${base}/estimate`, { status: 403, json: { detail: "You cannot export this." } }]]);
    open();
    fireEvent.click(screen.getByRole("button", { name: "Calculate Size" }));
    expect(await screen.findByText("You cannot export this.")).toBeInTheDocument();
  });

  test("a direct download saves the server-named archive with the request's fields", async () => {
    const { requests } = fakeServer([
      [`${base}/estimate`, { json: ESTIMATE }],
      [`${base}/data`, { text: "zip", headers: { "Content-Disposition": 'attachment; filename="named.zip"' } }],
    ]);
    await toSizeStep();
    fireEvent.click(screen.getByRole("button", { name: "Download Now" }));

    await waitFor(() => expect(saved).toEqual(["named.zip"]));
    const post = requests.find((r) => r.url.endsWith(`${base}/data`))!;
    expect(post.method).toBe("POST");
    expect(post.headers.Authorization).toBe("Bearer live.jwt");
    expect(JSON.parse(post.body as string)).toEqual({
      delivery_method: "direct",
      include_fastq: false,
      include_provenance: true,
    });
  });

  test("the old archive name is the fallback when the server names none", async () => {
    fakeServer([[`${base}/estimate`, { json: ESTIMATE }], [`${base}/data`, { text: "zip" }]]);
    await toSizeStep();
    fireEvent.click(screen.getByRole("button", { name: "Download Now" }));
    await waitFor(() => expect(saved).toEqual([fallback]));
  });

  test("a signed link is shown, and the link is not authenticated with the session", async () => {
    fakeServer([
      [`${base}/estimate`, { json: ESTIMATE }],
      [`${base}/data`, { json: { signed_url: "https://storage.example.com/x?sig=1", expires_in_hours: 24 } }],
    ]);
    capabilitiesCache.value = { ...MINIMAL_CAPABILITIES, signed_url_upload: true };
    await toSizeStep();
    fireEvent.click(screen.getByRole("button", { name: "Get Download Link" }));

    expect(await screen.findByText("https://storage.example.com/x?sig=1")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Download" })).toHaveAttribute(
      "href",
      "https://storage.example.com/x?sig=1",
    );
  });

  test("an expired session during export ends the session and saves nothing", async () => {
    fakeServer([
      [`${base}/estimate`, { json: ESTIMATE }],
      [`${base}/data`, { status: 401, json: { detail: "Unauthorized" } }],
    ]);
    await toSizeStep();
    fireEvent.click(screen.getByRole("button", { name: "Download Now" }));

    await waitFor(() => expect(mockNavigateToLogin).toHaveBeenCalled());
    expect(getToken()).toBeNull();
    expect(saved).toEqual([]);
  });
});

describe("the GEO export modal", () => {
  const RUNS = { runs: [{ id: 11, status: "completed", pipeline_name: "rnaseq", created_at: "2026-01-01" }], total: 1 };

  test("downloads with the session credential and the server's filename", async () => {
    const { requests } = fakeServer([
      ["/api/pipeline-runs?experiment_id=4", { json: RUNS }],
      ["/export/geo", { text: "zip", headers: { "Content-Disposition": 'attachment; filename="geo_4.zip"' } }],
    ]);
    render(<GeoExportModal experimentId={4} isOpen onClose={() => {}} userRole="admin" />);
    const button = await screen.findByRole("button", { name: /Download Export/ });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);

    await waitFor(() => expect(saved).toEqual(["geo_4.zip"]));
    const post = requests.find((r) => r.url.includes("/export/geo"))!;
    expect(post.url).toBe("https://api.lab.org/api/experiments/4/export/geo?pipeline_run_id=11&qc_status_filter=pass");
    expect(post.method).toBe("POST");
    expect(post.headers.Authorization).toBe("Bearer live.jwt");
  });

  test("a refused export shows the backend's explanation", async () => {
    fakeServer([
      ["/api/pipeline-runs?experiment_id=4", { json: RUNS }],
      ["/export/geo", { status: 422, json: { detail: "Required GEO fields are missing." } }],
    ]);
    render(<GeoExportModal experimentId={4} isOpen onClose={() => {}} userRole="admin" />);
    const button = await screen.findByRole("button", { name: /Download Export/ });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);

    expect(await screen.findByText("Required GEO fields are missing.")).toBeInTheDocument();
    expect(saved).toEqual([]);
  });
});
