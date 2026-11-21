/**
 * The Plot Archive says when new plots are not being added and why (storage lifecycle 11H;
 * signed-off mockup M13). Everyone who can open the archive sees it; only admins get Check again.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { PlotScanNotice } from "@/components/plots/PlotScanNotice";

let admin = true;
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({
    canAccess: (r: string, a: string) => admin && r === "infrastructure" && (a === "deploy" || a === "configure"),
    loading: false,
    failed: false,
  }),
}));
const mockPost = jest.fn();
jest.mock("@/lib/api", () => ({ api: { post: (...a: unknown[]) => mockPost(...a) } }));
jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a>,
}));

const LAST = "2026-11-19T14:05:00Z";
const lastText = new Date(LAST).toLocaleString("en-US", {
  month: "short",
  day: "numeric",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});
const inMinutes = (n: number) => new Date(Date.now() + n * 60_000 + 5_000).toISOString();

beforeEach(() => {
  admin = true;
  mockPost.mockReset();
});

test("healthy scanning shows nothing", () => {
  const { container } = render(<PlotScanNotice health={{ status: "ok", last_success_at: LAST }} />);
  expect(container).toBeEmptyDOMElement();
});

test.each([
  ["missing", "bioAF cannot find the results storage."],
  ["no_access", "bioAF is not allowed to read the results storage."],
  ["not_confirmed", "bioAF could not confirm the results storage belongs to this installation."],
] as const)("the %s cause is explained", (status, cause) => {
  admin = false;
  render(<PlotScanNotice health={{ status, last_success_at: LAST, next_attempt_at: inMinutes(25) }} />);
  expect(screen.getByText("New plots are not being added right now.")).toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent(
    `${cause} Plots already in the archive are not affected. Last successful check: ${lastText}. Ask an administrator to check the results storage.`,
  );
  expect(screen.queryByRole("button", { name: "Check again" })).not.toBeInTheDocument();
});

test("a temporary failure says when bioAF tries again", () => {
  admin = false;
  render(<PlotScanNotice health={{ status: "temporary", last_success_at: LAST, next_attempt_at: inMinutes(4) }} />);
  expect(screen.getByRole("status")).toHaveTextContent("The results storage did not respond. bioAF will try again in 4 minutes.");
});

test("admins see the next check and can check again", async () => {
  mockPost.mockResolvedValue({ status: "missing", last_success_at: LAST, next_attempt_at: inMinutes(30) });
  render(<PlotScanNotice health={{ status: "missing", last_success_at: LAST, next_attempt_at: inMinutes(25) }} />);
  expect(screen.getByRole("status")).toHaveTextContent("Next automatic check in 25 minutes.");
  expect(screen.getByRole("link", { name: "Open Components" })).toHaveAttribute("href", "/infrastructure/components");

  fireEvent.click(screen.getByRole("button", { name: "Check again" }));
  await waitFor(() => expect(mockPost).toHaveBeenCalledWith("/api/plots/scan/check", {}));
  expect(await screen.findByText("Checked just now. The results storage is still unavailable.")).toBeInTheDocument();
});

test("a check that finds the storage back clears the notice", async () => {
  const onRecovered = jest.fn();
  mockPost.mockResolvedValue({ status: "ok", last_success_at: new Date().toISOString() });
  const { container } = render(
    <PlotScanNotice
      health={{ status: "missing", last_success_at: LAST, next_attempt_at: inMinutes(25) }}
      onRecovered={onRecovered}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Check again" }));
  await waitFor(() => expect(container).toBeEmptyDOMElement());
  expect(onRecovered).toHaveBeenCalled();
});
