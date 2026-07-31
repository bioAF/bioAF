import { render, screen, act, waitFor, fireEvent } from "@testing-library/react";
import SettingsInfoPage from "@/app/(app)/settings/info/page";

jest.mock("next/navigation", () => ({
  usePathname: () => "/settings/info",
  useRouter: () => ({ push: jest.fn() }),
}));

jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true }),
}));


const mockApiGet = jest.fn();
const mockApiPost = jest.fn();

jest.mock("@/lib/api", () => ({
  api: {
    get: (...args: unknown[]) => mockApiGet(...args),
    post: (...args: unknown[]) => mockApiPost(...args),
  },
}));

const versionCheck = {
  current_version: "0.7.3",
  latest_version: "0.7.4",
  update_available: true,
  changelog: null,
  release_url: null,
};

const historyEmpty = { upgrades: [] };

describe("Settings Info page -- update flow", () => {
  beforeEach(() => {
    mockApiGet.mockReset();
    mockApiPost.mockReset();
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it("re-attaches to an in-progress update when the page is mounted", async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === "/api/upgrades/check") return Promise.resolve(versionCheck);
      if (url === "/api/upgrades/history") return Promise.resolve(historyEmpty);
      if (url === "/api/upgrades/status") {
        return Promise.resolve({
          status: "in_progress",
          step: "build",
          to_version: "0.7.4",
          started_at: new Date().toISOString(),
        });
      }
      return Promise.resolve({});
    });

    await act(async () => {
      render(<SettingsInfoPage />);
    });

    await waitFor(() => {
      expect(screen.getByText(/Updating to 0\.7\.4/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/Rebuilding containers/i)).toBeInTheDocument();
  });

  it("shows a 60-second reboot countdown during the warn step", async () => {
    jest.useFakeTimers({ doNotFake: ["nextTick"] });
    const warnStartedAt = new Date("2026-04-15T12:00:00.000Z");
    jest.setSystemTime(warnStartedAt);

    mockApiGet.mockImplementation((url: string) => {
      if (url === "/api/upgrades/check") return Promise.resolve(versionCheck);
      if (url === "/api/upgrades/history") return Promise.resolve(historyEmpty);
      if (url === "/api/upgrades/status") {
        return Promise.resolve({
          status: "in_progress",
          step: "warn",
          to_version: "0.7.4",
          started_at: warnStartedAt.toISOString(),
        });
      }
      return Promise.resolve({});
    });

    await act(async () => {
      render(<SettingsInfoPage />);
    });

    // Let the initial load promises resolve
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(screen.getByText(/Restarting in 60s/)).toBeInTheDocument();

    // Advance the clock by 15s; the 1s ticker should fire and update the countdown.
    await act(async () => {
      jest.advanceTimersByTime(15_000);
    });
    expect(screen.getByText(/Restarting in 45s/)).toBeInTheDocument();
  });

  it("clicking the Check for updates button hits /check with force=true", async () => {
    const checkResponses = [
      { ...versionCheck, latest_version: "0.7.4" },        // initial page load
      { ...versionCheck, latest_version: "0.7.5" },        // forced refresh
    ];
    mockApiGet.mockImplementation((url: string) => {
      if (url === "/api/upgrades/check" || url === "/api/upgrades/check?force=true") {
        return Promise.resolve(checkResponses.shift());
      }
      if (url === "/api/upgrades/history") return Promise.resolve(historyEmpty);
      if (url === "/api/upgrades/status") return Promise.resolve({ status: "idle" });
      return Promise.resolve({});
    });

    await act(async () => {
      render(<SettingsInfoPage />);
    });
    await waitFor(() => {
      expect(screen.getByText("0.7.4")).toBeInTheDocument();
    });

    // Initial page load must NOT pass force -- it's the cheap cached path.
    expect(mockApiGet).toHaveBeenCalledWith("/api/upgrades/check");
    expect(mockApiGet).not.toHaveBeenCalledWith("/api/upgrades/check?force=true");

    const button = screen.getByRole("button", { name: /Check for updates/i });
    await act(async () => {
      fireEvent.click(button);
    });

    // Click MUST pass force=true so the user gets a fresh GitHub query.
    await waitFor(() => {
      expect(mockApiGet).toHaveBeenCalledWith("/api/upgrades/check?force=true");
    });
    // And the new version is reflected in the UI.
    await waitFor(() => {
      expect(screen.getByText("0.7.5")).toBeInTheDocument();
    });
  });

  it("does not attach polling when no update is in progress", async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === "/api/upgrades/check") return Promise.resolve(versionCheck);
      if (url === "/api/upgrades/history") return Promise.resolve(historyEmpty);
      if (url === "/api/upgrades/status") return Promise.resolve({ status: "idle" });
      return Promise.resolve({});
    });

    await act(async () => {
      render(<SettingsInfoPage />);
    });

    await waitFor(() => {
      expect(screen.getByText(/Current Version:/i)).toBeInTheDocument();
    });
    expect(screen.queryByText(/Updating to/i)).not.toBeInTheDocument();
  });
});
