import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ConnectButton } from "@/components/shared/ConnectButton";
import { ApiError } from "@/lib/api";

const mockApiPost = jest.fn();
jest.mock("@/lib/api", () => {
  const actual = jest.requireActual("@/lib/api");
  return { ...actual, api: { post: (...args: unknown[]) => mockApiPost(...args) } };
});

const perms = { canAccess: jest.fn(), loading: false, failed: false };
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({
    canAccess: (...args: unknown[]) => perms.canAccess(...args),
    loading: perms.loading,
    failed: perms.failed,
    roleName: "custom-role",
    permissions: new Set(),
  }),
}));

const caps = { has: jest.fn(), loading: false };
jest.mock("@/hooks/useCapabilities", () => ({
  useCapabilities: () => ({ has: (flag: string) => caps.has(flag), capabilities: {}, loading: caps.loading }),
}));

const connection = {
  command:
    "kubectl exec -it --context gke_p_us-west1_c -n bioaf-pipelines bioaf-pipeline-1-17-x --container pipeline -- /bin/sh",
  setup_guide: "First-time setup for terminal access (Google Kubernetes Engine):\n\n1. Install kubectl",
  warning: "Commands you run and changes you make in this terminal are not tracked by bioAF.",
  target_type: "pipeline_job",
  target_id: "bioaf-pipeline-1-17",
  namespace: "bioaf-pipelines",
  pod_name: "bioaf-pipeline-1-17-x",
  container: "pipeline",
  context: "gke_p_us-west1_c",
};

function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const refusal = (code: string, reason: string, status = 409) =>
  new ApiError(status, "server detail that must not be shown", code, { reason });

describe("ConnectButton", () => {
  beforeEach(() => {
    mockApiPost.mockReset();
    mockApiPost.mockResolvedValue(connection);
    perms.canAccess.mockReset();
    perms.canAccess.mockImplementation((r: string, a: string) => r === "notebooks" && a === "launch");
    perms.loading = false;
    perms.failed = false;
    caps.has.mockReset();
    caps.has.mockReturnValue(true);
    caps.loading = false;
    jest.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    (console.error as jest.Mock).mockRestore();
  });

  it("follows the effective notebooks:launch permission, not the role name", () => {
    render(<ConnectButton targetType="pipeline_run" targetId={1} eligible />);
    expect(screen.getByRole("heading", { name: "Terminal instructions" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show instructions" })).toBeEnabled();
    expect(perms.canAccess).toHaveBeenCalledWith("notebooks", "launch");
  });

  it("is hidden without the permission", () => {
    perms.canAccess.mockReturnValue(false);
    render(<ConnectButton targetType="pipeline_run" targetId={1} eligible />);
    expect(screen.queryByText("Terminal instructions")).not.toBeInTheDocument();
  });

  it("is hidden when the backend lacks the ssh_exec capability", () => {
    caps.has.mockImplementation((flag: string) => flag !== "ssh_exec");
    render(<ConnectButton targetType="session" targetId={1} eligible />);
    expect(screen.queryByText("Terminal instructions")).not.toBeInTheDocument();
  });

  it("is hidden when the record is not eligible", () => {
    render(<ConnectButton targetType="session" targetId={1} eligible={false} />);
    expect(screen.queryByText("Terminal instructions")).not.toBeInTheDocument();
  });

  it.each([
    ["permissions", () => (perms.loading = true)],
    ["capabilities", () => (caps.loading = true)],
  ])("stays inactive while %s are loading", (_what, setLoading) => {
    setLoading();
    render(<ConnectButton targetType="pipeline_run" targetId={1} eligible />);
    expect(screen.getByRole("button", { name: "Show instructions" })).toBeDisabled();
  });

  it("does not claim the user lacks permission when permissions failed to load", () => {
    perms.failed = true;
    perms.canAccess.mockReturnValue(false);
    render(<ConnectButton targetType="pipeline_run" targetId={1} eligible />);
    expect(screen.queryByText(/not allowed|permission/i)).not.toBeInTheDocument();
    expect(screen.queryByText("Terminal instructions")).not.toBeInTheDocument();
  });

  it("requests instructions for the record and shows command, target, warning, and guidance", async () => {
    const pending = deferred<typeof connection>();
    mockApiPost.mockReturnValueOnce(pending.promise);
    render(<ConnectButton targetType="pipeline_run" targetId={7} eligible />);

    fireEvent.click(screen.getByRole("button", { name: "Show instructions" }));
    expect(screen.getByRole("button", { name: "Preparing instructions..." })).toBeDisabled();
    await act(async () => pending.resolve(connection));

    expect(mockApiPost).toHaveBeenCalledWith("/api/pipeline-runs/7/connect", undefined, expect.anything());
    expect(screen.getByTestId("connect-command")).toHaveTextContent(connection.command);
    expect(screen.getByTestId("connect-warning")).toHaveTextContent("not tracked by bioAF");
    expect(screen.getByTestId("connect-target")).toHaveTextContent("bioaf-pipeline-1-17");
    expect(screen.getByTestId("connect-target")).toHaveTextContent("bioaf-pipelines");
    expect(screen.getByTestId("connect-guidance")).toHaveTextContent(/does not open a terminal/);
    expect(screen.getByTestId("connect-guidance")).toHaveTextContent(/refresh .* details and request new instructions/i);
  });

  it("keeps the exact target in collapsed connection details, each with a plain definition", async () => {
    render(<ConnectButton targetType="pipeline_run" targetId={7} eligible />);
    fireEvent.click(screen.getByRole("button", { name: "Show instructions" }));
    const target = await screen.findByTestId("connect-target");

    const details = target.closest("details");
    expect(details).not.toBeNull();
    expect(details).not.toHaveAttribute("open");
    expect(within(details!).getByText("Connection details").tagName).toBe("SUMMARY");

    const entry = (label: string) => within(target).getByText(label).nextElementSibling?.textContent ?? "";
    const rows: [string, string, RegExp][] = [
      ["Workload", "bioaf-pipeline-1-17", /Job or Pod bioAF started for this analysis/],
      ["Pod", "bioaf-pipeline-1-17-x", /running copy of that workload the command opens a shell in/],
      ["Container", "pipeline", /main program inside the pod \(not a helper\)/],
      ["Namespace", "bioaf-pipelines", /section of the cluster this workload runs in/],
      ["Cluster context", "gke_p_us-west1_c", /name kubectl uses for this cluster after the first-time setup/],
    ];
    for (const [label, value, definition] of rows) {
      expect(entry(label)).toContain(value);
      expect(entry(label)).toMatch(definition);
    }
  });

  it("uses the session endpoint for notebook sessions", async () => {
    render(<ConnectButton targetType="session" targetId={3} eligible />);
    fireEvent.click(screen.getByRole("button", { name: "Show instructions" }));
    await screen.findByTestId("connect-command");
    expect(mockApiPost).toHaveBeenCalledWith("/api/sessions/3/connect", undefined, expect.anything());
  });

  it("discloses the setup guide on request", async () => {
    render(<ConnectButton targetType="pipeline_run" targetId={1} eligible />);
    fireEvent.click(screen.getByRole("button", { name: "Show instructions" }));
    await screen.findByTestId("connect-command");
    expect(screen.queryByTestId("setup-guide-content")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("setup-guide-toggle"));
    expect(screen.getByTestId("setup-guide-content")).toHaveTextContent("Install kubectl");
    fireEvent.click(screen.getByTestId("setup-guide-toggle"));
    expect(screen.queryByTestId("setup-guide-content")).not.toBeInTheDocument();
  });

  it("copies the command and confirms it without another request", async () => {
    const writeText = jest.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    render(<ConnectButton targetType="pipeline_run" targetId={1} eligible />);
    fireEvent.click(screen.getByRole("button", { name: "Show instructions" }));
    await screen.findByTestId("connect-command");

    await act(async () => fireEvent.click(screen.getByTestId("copy-button")));

    expect(writeText).toHaveBeenCalledWith(connection.command);
    expect(screen.getByTestId("copy-button")).toHaveTextContent("Copied");
    expect(mockApiPost).toHaveBeenCalledTimes(1);
  });

  it("says so when copying fails and keeps the command selectable", async () => {
    const writeText = jest.fn().mockRejectedValue(new Error("NotAllowedError"));
    Object.assign(navigator, { clipboard: { writeText } });
    render(<ConnectButton targetType="pipeline_run" targetId={1} eligible />);
    fireEvent.click(screen.getByRole("button", { name: "Show instructions" }));
    await screen.findByTestId("connect-command");

    await act(async () => fireEvent.click(screen.getByTestId("copy-button")));

    expect(screen.getByTestId("copy-error")).toHaveTextContent(
      "We couldn't copy the command. Select it and copy it manually.",
    );
    expect(screen.getByTestId("connect-command")).toHaveClass("select-all");

    writeText.mockResolvedValueOnce(undefined);
    await act(async () => fireEvent.click(screen.getByTestId("copy-button")));
    expect(screen.queryByTestId("copy-error")).not.toBeInTheDocument();
  });

  it("refreshes the page record and explains a state refusal", async () => {
    const onStateRefused = jest.fn();
    mockApiPost.mockRejectedValueOnce(refusal("connection_unavailable", "not_running"));
    render(<ConnectButton targetType="pipeline_run" targetId={1} eligible onStateRefused={onStateRefused} />);
    fireEvent.click(screen.getByRole("button", { name: "Show instructions" }));

    expect(await screen.findByTestId("connect-error")).toHaveTextContent(
      "This analysis is no longer running. Refresh its details before requesting new instructions.",
    );
    expect(onStateRefused).toHaveBeenCalled();
    expect(screen.queryByText(/server detail/)).not.toBeInTheDocument();
  });

  it.each([
    ["connection_provider_unavailable", "provider_unavailable", 502, /could not check this analysis with the cluster right now/],
    ["connection_provider_timeout", "deadline_exceeded", 504, /could not check this analysis with the cluster right now/],
    ["connection_provider_unavailable", "provider_denied", 502, /Ask an administrator to review bioAF's cluster access/],
    ["connection_target_invalid", "cluster_setup_incomplete", 409, /Ask an administrator to finish cluster setup/],
  ])("distinguishes %s/%s from a stopped analysis", async (code, reason, status, message) => {
    const onStateRefused = jest.fn();
    mockApiPost.mockRejectedValueOnce(refusal(code, reason, status));
    render(<ConnectButton targetType="pipeline_run" targetId={1} eligible onStateRefused={onStateRefused} />);
    fireEvent.click(screen.getByRole("button", { name: "Show instructions" }));

    const error = await screen.findByTestId("connect-error");
    expect(error).toHaveTextContent(message);
    expect(error).not.toHaveTextContent(/no longer running/);
    expect(error).not.toHaveTextContent(/server detail/);
    expect(onStateRefused).not.toHaveBeenCalled();
  });

  it("clears a stale error when retrying", async () => {
    mockApiPost.mockRejectedValueOnce(refusal("connection_provider_timeout", "deadline_exceeded", 504));
    render(<ConnectButton targetType="pipeline_run" targetId={1} eligible />);
    fireEvent.click(screen.getByRole("button", { name: "Show instructions" }));
    await screen.findByTestId("connect-error");

    fireEvent.click(screen.getByRole("button", { name: "Show instructions" }));
    expect(screen.queryByTestId("connect-error")).not.toBeInTheDocument();
    await screen.findByTestId("connect-command");
  });

  it("clears expanded instructions when the target changes", async () => {
    const { rerender } = render(<ConnectButton targetType="pipeline_run" targetId={1} eligible />);
    fireEvent.click(screen.getByRole("button", { name: "Show instructions" }));
    await screen.findByTestId("connect-command");

    rerender(<ConnectButton targetType="pipeline_run" targetId={2} eligible />);

    expect(screen.queryByTestId("connect-command")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show instructions" })).toBeEnabled();
  });

  it("clears expanded instructions when the target becomes ineligible", async () => {
    const { rerender } = render(<ConnectButton targetType="session" targetId={1} eligible />);
    fireEvent.click(screen.getByRole("button", { name: "Show instructions" }));
    await screen.findByTestId("connect-command");

    rerender(<ConnectButton targetType="session" targetId={1} eligible={false} />);
    expect(screen.queryByTestId("connect-command")).not.toBeInTheDocument();

    rerender(<ConnectButton targetType="session" targetId={1} eligible />);
    expect(screen.queryByTestId("connect-command")).not.toBeInTheDocument();
  });

  it("clears expanded instructions when the permission is lost", async () => {
    const { rerender } = render(<ConnectButton targetType="session" targetId={1} eligible />);
    fireEvent.click(screen.getByRole("button", { name: "Show instructions" }));
    await screen.findByTestId("connect-command");

    perms.canAccess.mockReturnValue(false);
    rerender(<ConnectButton targetType="session" targetId={1} eligible />);
    expect(screen.queryByTestId("connect-command")).not.toBeInTheDocument();

    perms.canAccess.mockImplementation((r: string, a: string) => r === "notebooks" && a === "launch");
    rerender(<ConnectButton targetType="session" targetId={1} eligible />);
    expect(screen.queryByTestId("connect-command")).not.toBeInTheDocument();
  });

  it("never shows a late response for an earlier target under a new one", async () => {
    const first = deferred<typeof connection>();
    mockApiPost.mockReturnValueOnce(first.promise);
    const { rerender } = render(<ConnectButton targetType="pipeline_run" targetId={1} eligible />);
    fireEvent.click(screen.getByRole("button", { name: "Show instructions" }));

    rerender(<ConnectButton targetType="pipeline_run" targetId={2} eligible />);
    await act(async () => first.resolve(connection));

    expect(screen.queryByTestId("connect-command")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show instructions" })).toBeEnabled();
    const signal = mockApiPost.mock.calls[0][2].signal as AbortSignal;
    expect(signal.aborted).toBe(true);
  });

  it("never shows a late failure for an earlier target under a new one", async () => {
    const first = deferred<typeof connection>();
    mockApiPost.mockReturnValueOnce(first.promise);
    const { rerender } = render(<ConnectButton targetType="session" targetId={1} eligible />);
    fireEvent.click(screen.getByRole("button", { name: "Show instructions" }));

    rerender(<ConnectButton targetType="session" targetId={2} eligible />);
    await act(async () => first.reject(refusal("connection_unavailable", "not_running")));

    await waitFor(() => expect(screen.queryByTestId("connect-error")).not.toBeInTheDocument());
  });
});
