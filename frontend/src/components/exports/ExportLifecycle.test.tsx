/**
 * The experiment and project export dialogs share one lifecycle. These tests drive both real
 * wrappers through the real API client, with a fetch stand-in whose replies can be held back to
 * reproduce races (close, reopen, entity and session changes while work is pending).
 */
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { fakeResponse, type FakeReply } from "@/testing/fakeServer";
import { startSession } from "@/lib/session";
import { DataExportModal } from "@/components/experiments/DataExportModal";
import { ProjectExportModal } from "@/components/projects/ProjectExportModal";

jest.mock("@/lib/navigation", () => ({ navigateToLogin: jest.fn() }));

interface Req {
  url: string;
  method: string;
  body: unknown;
}

type Handler = (req: Req) => FakeReply | Promise<FakeReply>;

let requests: Req[];
let saved: string[];

/**
 * Routes match by URL substring. A handler may return a promise to hold its reply. By default a
 * held reply is abandoned when the request is aborted, as a browser does; `lateReplies` delivers
 * it anyway, to show the dialog ignores answers it no longer owns.
 */
function serve(routes: Array<[string, Handler]>, { lateReplies = false } = {}) {
  global.fetch = jest.fn((url: RequestInfo | URL, init: RequestInit = {}) => {
    const req: Req = { url: String(url), method: init.method ?? "GET", body: init.body };
    requests.push(req);
    const route = routes.find(([fragment]) => req.url.includes(fragment));
    const reply = route ? route[1](req) : { status: 404, json: { detail: "Not found" } };
    return new Promise<Response>((resolve, reject) => {
      const signal = init.signal;
      if (!lateReplies && signal) {
        if (signal.aborted) return reject(new DOMException("aborted", "AbortError"));
        signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
      }
      Promise.resolve(reply).then((r) => resolve(fakeResponse(r)));
    });
  }) as unknown as typeof fetch;
}

function held() {
  let release: (reply: FakeReply) => void = () => {};
  const promise = new Promise<FakeReply>((r) => (release = r));
  return { promise, release, handler: () => promise };
}

const realFetch = global.fetch;
const realClipboard = Object.getOwnPropertyDescriptor(navigator, "clipboard");

beforeEach(() => {
  requests = [];
  saved = [];
  startSession("live.jwt");
  (URL as unknown as { createObjectURL: unknown }).createObjectURL = jest.fn(() => "blob:x");
  (URL as unknown as { revokeObjectURL: unknown }).revokeObjectURL = jest.fn();
  jest.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
    saved.push(this.download);
  });
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  global.fetch = realFetch;
  if (realClipboard) Object.defineProperty(navigator, "clipboard", realClipboard);
  else delete (navigator as unknown as { clipboard?: unknown }).clipboard;
  jest.restoreAllMocks();
  jest.useRealTimers();
});

const LINKS_SUPPORTED: [string, Handler] = [
  "/api/bootstrap/status",
  () => ({ json: { capabilities: { signed_url_upload: true } } }),
];
const LINKS_UNSUPPORTED: [string, Handler] = [
  "/api/bootstrap/status",
  () => ({ json: { capabilities: { signed_url_upload: false } } }),
];

const KINDS = [
  {
    kind: "experiment",
    base: "/api/experiments/4/export",
    otherBase: "/api/experiments/5/export",
    estimate: { total_bytes: 2048, breakdown: { results: 1024, fastq: 1024 } },
    breakdownText: ["results", "fastq"],
    open: (props: { id?: number; isOpen?: boolean; onClose?: () => void; name?: string } = {}) => (
      <DataExportModal
        experimentId={props.id ?? 4}
        experimentName={props.name ?? "Liver atlas"}
        isOpen={props.isOpen ?? true}
        onClose={props.onClose ?? (() => {})}
      />
    ),
  },
  {
    kind: "project",
    base: "/api/projects/4/export",
    otherBase: "/api/projects/5/export",
    estimate: {
      total_bytes: 2048,
      breakdown: { results: 2048 },
      experiments: [
        { experiment_id: 1, name: "Run A", total_bytes: 1024 },
        { experiment_id: 2, name: "Run B", total_bytes: 1024 },
      ],
    },
    breakdownText: ["Run A", "Run B"],
    open: (props: { id?: number; isOpen?: boolean; onClose?: () => void; name?: string } = {}) => (
      <ProjectExportModal
        projectId={props.id ?? 4}
        projectName={props.name ?? "Liver atlas"}
        isOpen={props.isOpen ?? true}
        onClose={props.onClose ?? (() => {})}
      />
    ),
  },
];

describe.each(KINDS)("the $kind export dialog", ({ base, otherBase, estimate, breakdownText, open }) => {
  const estimateRoute = (body: unknown = estimate): [string, Handler] => [`${base}/estimate`, () => ({ json: body })];

  async function toSizeStep() {
    fireEvent.click(screen.getByRole("button", { name: "Calculate Size" }));
    await screen.findByRole("button", { name: "Download Now" });
  }

  const posts = () => requests.filter((r) => r.method === "POST");

  describe("options and estimate", () => {
    test("starts with FASTQ off and provenance on, and sends the chosen options", async () => {
      serve([estimateRoute(), LINKS_SUPPORTED, [`${base}/data`, () => ({ text: "zip" })]]);
      render(open());
      const fastq = screen.getByRole("checkbox", { name: "Include FASTQ files" });
      const provenance = screen.getByRole("checkbox", { name: "Include provenance report" });
      expect(fastq).not.toBeChecked();
      expect(provenance).toBeChecked();

      fireEvent.click(fastq);
      fireEvent.click(provenance);
      await toSizeStep();
      expect(requests[0].url).toContain(`${base}/estimate?include_fastq=true`);

      fireEvent.click(screen.getByRole("button", { name: "Download Now" }));
      await waitFor(() => expect(posts()).toHaveLength(1));
      expect(JSON.parse(posts()[0].body as string)).toEqual({
        delivery_method: "direct",
        include_fastq: true,
        include_provenance: false,
      });
    });

    test("shows the total and the breakdown", async () => {
      serve([estimateRoute(), LINKS_SUPPORTED]);
      render(open());
      await toSizeStep();
      expect(screen.getByText("Total size")).toBeInTheDocument();
      expect(screen.getByText("2.0 KB")).toBeInTheDocument();
      for (const text of breakdownText) expect(screen.getByText(text)).toBeInTheDocument();
    });

    test("an empty export reads as zero bytes", async () => {
      const empty = { ...estimate, total_bytes: 0, breakdown: {}, ...("experiments" in estimate ? { experiments: [] } : {}) };
      serve([estimateRoute(empty), LINKS_SUPPORTED]);
      render(open());
      await toSizeStep();
      expect(screen.getByText("0 B")).toBeInTheDocument();
    });

    test("a malformed estimate is a failure, not a size", async () => {
      serve([estimateRoute({ total_bytes: -1, breakdown: "lots" }), LINKS_SUPPORTED]);
      render(open());
      fireEvent.click(screen.getByRole("button", { name: "Calculate Size" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("The size estimate could not be read.");
      expect(screen.queryByRole("button", { name: "Download Now" })).not.toBeInTheDocument();
    });

    test("names render as text, never as markup", async () => {
      serve([estimateRoute(), LINKS_SUPPORTED]);
      const { container } = render(open({ name: "<b>bold</b>" }));
      expect(screen.getByText("<b>bold</b>")).toBeInTheDocument();
      expect(container.querySelector("b")).toBeNull();
    });
  });

  describe("signed-link capability", () => {
    test("supported: the link is enabled, and a large export recommends it", async () => {
      serve([estimateRoute({ ...estimate, total_bytes: 2 * 1024 ** 3 }), LINKS_SUPPORTED]);
      render(open());
      await toSizeStep();
      await waitFor(() => expect(screen.getByRole("button", { name: "Get Download Link" })).toBeEnabled());
      expect(screen.getByText(/We recommend using .Get Download Link./)).toBeInTheDocument();
    });

    test("unsupported: the link is disabled with a reason, and direct download still works", async () => {
      serve([
        estimateRoute({ ...estimate, total_bytes: 2 * 1024 ** 3 }),
        LINKS_UNSUPPORTED,
        [`${base}/data`, () => ({ text: "zip" })],
      ]);
      render(open());
      await toSizeStep();
      const link = screen.getByRole("button", { name: "Get Download Link" });
      await waitFor(() =>
        expect(link).toHaveAccessibleDescription(/Download links are unavailable for this storage, or support could not be confirmed/),
      );
      expect(link).toBeDisabled();
      expect(screen.queryByText(/We recommend using .Get Download Link./)).not.toBeInTheDocument();
      expect(screen.getByText(/larger than 1 GB/)).toBeInTheDocument();

      fireEvent.click(link);
      expect(posts()).toHaveLength(0);

      fireEvent.click(screen.getByRole("button", { name: "Download Now" }));
      await waitFor(() => expect(saved).toHaveLength(1));
    });

    test("loading: the link stays disabled until support is confirmed", async () => {
      const capabilities = held();
      serve([estimateRoute(), ["/api/bootstrap/status", capabilities.handler]]);
      render(open());
      await toSizeStep();
      const link = screen.getByRole("button", { name: "Get Download Link" });
      expect(link).toBeDisabled();
      expect(link).toHaveAccessibleDescription(/could not be confirmed/);

      await act(async () => capabilities.release({ json: { capabilities: { signed_url_upload: true } } }));
      await waitFor(() => expect(link).toBeEnabled());
    });

    test("a failed capability read stays unconfirmed when the dialog is reopened", async () => {
      serve([estimateRoute(), ["/api/bootstrap/status", () => ({ status: 500, json: { detail: "down" } })]]);
      const view = render(open());
      await toSizeStep();
      await waitFor(() =>
        expect(screen.getByRole("button", { name: "Get Download Link" })).toHaveAccessibleDescription(/could not be confirmed/),
      );

      view.rerender(open({ isOpen: false }));
      view.rerender(open());
      await toSizeStep();
      expect(screen.getByRole("button", { name: "Get Download Link" })).toBeDisabled();
      expect(requests.filter((r) => r.url.includes("/api/bootstrap/status"))).toHaveLength(1);
    });
  });

  describe("delivery", () => {
    test("one POST however fast the actions are pressed, including competing ones", async () => {
      const reply = held();
      serve([estimateRoute(), LINKS_SUPPORTED, [`${base}/data`, reply.handler]]);
      render(open());
      await toSizeStep();
      const direct = screen.getByRole("button", { name: "Download Now" });
      const link = screen.getByRole("button", { name: "Get Download Link" });
      await waitFor(() => expect(link).toBeEnabled());

      act(() => {
        direct.click();
        direct.click();
        link.click();
      });
      expect(posts()).toHaveLength(1);
      await act(async () => reply.release({ text: "zip" }));
      await waitFor(() => expect(saved).toHaveLength(1));
    });

    test("a signed link shows its expiry from the response", async () => {
      serve([
        estimateRoute(),
        LINKS_SUPPORTED,
        [`${base}/data`, () => ({ json: { signed_url: "https://files.lab.org/a?sig=1", expires_in_hours: 6 } })],
      ]);
      render(open());
      await toSizeStep();
      await waitFor(() => expect(screen.getByRole("button", { name: "Get Download Link" })).toBeEnabled());
      fireEvent.click(screen.getByRole("button", { name: "Get Download Link" }));

      expect(await screen.findByText("https://files.lab.org/a?sig=1")).toBeInTheDocument();
      expect(screen.getByText("This link expires in 6 hours.")).toBeInTheDocument();
      expect(JSON.parse(posts()[0].body as string).delivery_method).toBe("gcs");
      const anchor = screen.getByRole("link", { name: "Download" });
      expect(anchor).toHaveAttribute("rel", "noopener noreferrer");
      expect(anchor).toHaveAttribute("target", "_blank");
    });

    test.each([
      ["missing", undefined],
      ["zero", 0],
      ["negative", -3],
      ["not a number", "soon"],
      ["not finite", "Infinity"],
    ])("an expiry that is %s omits only the expiry line", async (_label, expires) => {
      serve([
        estimateRoute(),
        LINKS_SUPPORTED,
        [`${base}/data`, () => ({ json: { signed_url: "https://files.lab.org/a", expires_in_hours: expires } })],
      ]);
      render(open());
      await toSizeStep();
      await waitFor(() => expect(screen.getByRole("button", { name: "Get Download Link" })).toBeEnabled());
      fireEvent.click(screen.getByRole("button", { name: "Get Download Link" }));

      expect(await screen.findByText("https://files.lab.org/a")).toBeInTheDocument();
      expect(screen.queryByText(/expires/)).not.toBeInTheDocument();
      expect(screen.queryByText(/24 hours/)).not.toBeInTheDocument();
    });

    test.each([
      ["missing", {}],
      ["empty", { signed_url: "" }],
      ["an executable scheme", { signed_url: "javascript:alert(1)" }],
      ["not a URL", { signed_url: "files/a.zip" }],
    ])("a signed URL that is %s is a failure, never a download success", async (_label, body) => {
      serve([estimateRoute(), LINKS_SUPPORTED, [`${base}/data`, () => ({ json: body })]]);
      render(open());
      await toSizeStep();
      await waitFor(() => expect(screen.getByRole("button", { name: "Get Download Link" })).toBeEnabled());
      fireEvent.click(screen.getByRole("button", { name: "Get Download Link" }));

      expect(await screen.findByRole("alert")).toHaveTextContent("The download link could not be read.");
      expect(screen.queryByText(/Download started/)).not.toBeInTheDocument();
      expect(screen.queryByRole("link", { name: "Download" })).not.toBeInTheDocument();
    });

    test("a failed export shows the backend's reason and can be retried explicitly", async () => {
      let attempts = 0;
      serve([
        estimateRoute(),
        LINKS_SUPPORTED,
        [`${base}/data`, () => (++attempts === 1 ? { status: 500, json: { detail: "Storage is busy." } } : { text: "zip" })],
      ]);
      render(open());
      await toSizeStep();
      fireEvent.click(screen.getByRole("button", { name: "Download Now" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("Storage is busy.");
      expect(posts()).toHaveLength(1);

      fireEvent.click(screen.getByRole("button", { name: "Back" }));
      fireEvent.click(screen.getByRole("button", { name: "Download Now" }));
      await waitFor(() => expect(saved).toHaveLength(1));
      expect(posts()).toHaveLength(2);
      expect(screen.getByText(/Download started/)).toBeInTheDocument();
    });
  });

  describe("ownership", () => {
    test.each([false, true])(
      "closing while the estimate is pending discards it (late reply delivered: %s)",
      async (lateReplies) => {
        const reply = held();
        serve([[`${base}/estimate`, reply.handler], LINKS_SUPPORTED], { lateReplies });
        const view = render(open());
        fireEvent.click(screen.getByRole("button", { name: "Calculate Size" }));
        expect(screen.getByRole("button", { name: "Calculating..." })).toBeDisabled();

        view.rerender(open({ isOpen: false }));
        view.rerender(open());
        await act(async () => reply.release({ json: estimate }));

        expect(screen.queryByText("Total size")).not.toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Calculate Size" })).toBeEnabled();
      },
    );

    test.each([false, true])(
      "closing while a direct download is pending saves nothing (late reply delivered: %s)",
      async (lateReplies) => {
        const reply = held();
        serve([estimateRoute(), LINKS_SUPPORTED, [`${base}/data`, reply.handler]], { lateReplies });
        const view = render(open());
        await toSizeStep();
        fireEvent.click(screen.getByRole("button", { name: "Download Now" }));
        expect(screen.getByText("Preparing your export...")).toBeInTheDocument();

        view.rerender(open({ isOpen: false }));
        view.rerender(open());
        await act(async () => reply.release({ text: "zip" }));

        expect(saved).toEqual([]);
        expect(screen.queryByText(/Download started/)).not.toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Calculate Size" })).toBeEnabled();
      },
    );

    test("a late signed link or error from a closed opening never reaches the new one", async () => {
      const link = held();
      const failure = held();
      let call = 0;
      serve(
        [estimateRoute(), LINKS_SUPPORTED, [`${base}/data`, () => (++call === 1 ? link.promise : failure.promise)]],
        { lateReplies: true },
      );
      const view = render(open());
      await toSizeStep();
      await waitFor(() => expect(screen.getByRole("button", { name: "Get Download Link" })).toBeEnabled());
      fireEvent.click(screen.getByRole("button", { name: "Get Download Link" }));

      view.rerender(open({ isOpen: false }));
      view.rerender(open());
      await toSizeStep();
      fireEvent.click(screen.getByRole("button", { name: "Download Now" }));
      view.rerender(open({ isOpen: false }));
      view.rerender(open());

      await act(async () => {
        link.release({ json: { signed_url: "https://files.lab.org/old", expires_in_hours: 1 } });
        failure.release({ status: 500, json: { detail: "Old failure" } });
      });
      expect(screen.queryByText("https://files.lab.org/old")).not.toBeInTheDocument();
      expect(screen.queryByText("Old failure")).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Calculate Size" })).toBeEnabled();
      expect(saved).toEqual([]);
    });

    test("closing releases the busy state so the new opening can act at once", async () => {
      const reply = held();
      serve([[`${base}/estimate`, () => (requests.length === 1 ? reply.promise : { json: estimate })], LINKS_SUPPORTED]);
      const view = render(open());
      fireEvent.click(screen.getByRole("button", { name: "Calculate Size" }));
      view.rerender(open({ isOpen: false }));
      view.rerender(open());
      await toSizeStep();
      expect(screen.getByText("Total size")).toBeInTheDocument();
    });

    test("unmounting while a download is pending saves nothing", async () => {
      const reply = held();
      serve([estimateRoute(), LINKS_SUPPORTED, [`${base}/data`, reply.handler]], { lateReplies: true });
      const view = render(open());
      await toSizeStep();
      fireEvent.click(screen.getByRole("button", { name: "Download Now" }));
      view.unmount();
      await act(async () => reply.release({ text: "zip" }));
      expect(saved).toEqual([]);
    });

    test("an ordinary close keeps this entity's options and clears the result", async () => {
      serve([estimateRoute(), LINKS_SUPPORTED]);
      const view = render(open());
      fireEvent.click(screen.getByRole("checkbox", { name: "Include FASTQ files" }));
      await toSizeStep();

      view.rerender(open({ isOpen: false }));
      view.rerender(open());
      expect(screen.queryByText("Total size")).not.toBeInTheDocument();
      expect(screen.getByRole("checkbox", { name: "Include FASTQ files" })).toBeChecked();
    });

    test("a different entity starts from the defaults and never shows the old estimate", async () => {
      const reply = held();
      serve([[`${base}/estimate`, reply.handler], [`${otherBase}/estimate`, () => ({ json: estimate })], LINKS_SUPPORTED], {
        lateReplies: true,
      });
      const view = render(open());
      fireEvent.click(screen.getByRole("checkbox", { name: "Include FASTQ files" }));
      fireEvent.click(screen.getByRole("button", { name: "Calculate Size" }));

      view.rerender(open({ id: 5 }));
      expect(screen.getByRole("checkbox", { name: "Include FASTQ files" })).not.toBeChecked();
      await act(async () => reply.release({ json: estimate }));
      expect(screen.queryByText("Total size")).not.toBeInTheDocument();

      await toSizeStep();
      expect(requests.at(-2)?.url ?? "").toContain(`${otherBase}/estimate?include_fastq=false`);
    });

    test("changing an option while estimating discards the estimate for the old options", async () => {
      const reply = held();
      serve([[`${base}/estimate`, reply.handler], LINKS_SUPPORTED], { lateReplies: true });
      render(open());
      fireEvent.click(screen.getByRole("button", { name: "Calculate Size" }));
      fireEvent.click(screen.getByRole("checkbox", { name: "Include FASTQ files" }));
      await act(async () => reply.release({ json: estimate }));

      expect(screen.queryByText("Total size")).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Calculate Size" })).toBeEnabled();
    });

    test("Back returns to the options with the chosen values", async () => {
      serve([estimateRoute(), LINKS_SUPPORTED]);
      render(open());
      fireEvent.click(screen.getByRole("checkbox", { name: "Include provenance report" }));
      await toSizeStep();
      fireEvent.click(screen.getByRole("button", { name: "Back" }));
      expect(screen.getByRole("checkbox", { name: "Include provenance report" })).not.toBeChecked();
      expect(screen.queryByText("Total size")).not.toBeInTheDocument();
    });

    test("a new session starts from the defaults and abandons pending work", async () => {
      const reply = held();
      serve([[`${base}/estimate`, reply.handler], LINKS_SUPPORTED], { lateReplies: true });
      render(open());
      fireEvent.click(screen.getByRole("checkbox", { name: "Include FASTQ files" }));
      fireEvent.click(screen.getByRole("button", { name: "Calculate Size" }));

      act(() => startSession("other.jwt"));
      await act(async () => reply.release({ json: estimate }));

      expect(screen.queryByText("Total size")).not.toBeInTheDocument();
      expect(screen.getByRole("checkbox", { name: "Include FASTQ files" })).not.toBeChecked();
      expect(screen.getByRole("button", { name: "Calculate Size" })).toBeEnabled();
    });
  });

  describe("copying the link", () => {
    async function toLink() {
      serve([
        estimateRoute(),
        LINKS_SUPPORTED,
        [`${base}/data`, () => ({ json: { signed_url: "https://files.lab.org/a", expires_in_hours: 24 } })],
      ]);
      const view = render(open());
      await toSizeStep();
      await waitFor(() => expect(screen.getByRole("button", { name: "Get Download Link" })).toBeEnabled());
      fireEvent.click(screen.getByRole("button", { name: "Get Download Link" }));
      await screen.findByText("https://files.lab.org/a");
      return view;
    }

    function clipboard(writeText: jest.Mock | undefined) {
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value: writeText ? { writeText } : undefined,
      });
    }

    test("reads Copied! only after the clipboard accepts the link", async () => {
      let accept: () => void = () => {};
      const writeText = jest.fn(() => new Promise<void>((r) => (accept = r)));
      clipboard(writeText);
      await toLink();
      fireEvent.click(screen.getByRole("button", { name: "Copy Link" }));
      expect(screen.queryByRole("button", { name: "Copied!" })).not.toBeInTheDocument();
      await act(async () => accept());
      expect(screen.getByRole("button", { name: "Copied!" })).toBeInTheDocument();
      expect(writeText).toHaveBeenCalledWith("https://files.lab.org/a");
    });

    test.each([
      ["rejected", jest.fn(() => Promise.reject(new Error("denied")))],
      ["unavailable", undefined],
    ])("a %s clipboard says so and keeps the link usable", async (_label, writeText) => {
      clipboard(writeText as jest.Mock | undefined);
      await toLink();
      fireEvent.click(screen.getByRole("button", { name: "Copy Link" }));
      const dialog = screen.getByRole("dialog");
      expect(await within(dialog).findByText(/Copying is not available here/)).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Copied!" })).not.toBeInTheDocument();
      expect(screen.getByRole("link", { name: "Download" })).toHaveAttribute("href", "https://files.lab.org/a");
    });

    test("an earlier copy's timer cannot clear a later copy early", async () => {
      clipboard(jest.fn(() => Promise.resolve()));
      await toLink();
      jest.useFakeTimers();
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Copy Link" }));
      });
      act(() => jest.advanceTimersByTime(1500));
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Copied!" }));
      });
      act(() => jest.advanceTimersByTime(1000));
      expect(screen.getByRole("button", { name: "Copied!" })).toBeInTheDocument();
      act(() => jest.advanceTimersByTime(1500));
      expect(screen.getByRole("button", { name: "Copy Link" })).toBeInTheDocument();
    });

    test("closing ends the copied state and its timer", async () => {
      clipboard(jest.fn(() => Promise.resolve()));
      const view = await toLink();
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Copy Link" }));
      });
      view.rerender(open({ isOpen: false }));
      view.rerender(open());
      expect(screen.queryByRole("button", { name: "Copied!" })).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Calculate Size" })).toBeInTheDocument();
    });
  });
});

test("the project breakdown rejects malformed experiment rows", async () => {
  serve([
    [
      "/api/projects/4/export/estimate",
      () => ({ json: { total_bytes: 10, breakdown: {}, experiments: [{ experiment_id: 1, name: 7, total_bytes: "x" }] } }),
    ],
  ]);
  render(<ProjectExportModal projectId={4} projectName="P" isOpen onClose={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Calculate Size" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("The size estimate could not be read.");
});
