import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { NotificationBell } from "@/components/notifications/NotificationBell";

const mockApiGet = jest.fn();
const mockApiPost = jest.fn();
jest.mock("@/lib/api", () => ({
  api: {
    get: (...args: unknown[]) => mockApiGet(...args),
    post: (...args: unknown[]) => mockApiPost(...args),
  },
}));

// Prevent NotificationDropdown from fetching on render
jest.mock("@/components/notifications/NotificationDropdown", () => ({
  NotificationDropdown: ({ onClose }: { onClose: () => void }) => (
    <div data-testid="notification-dropdown">
      <button onClick={onClose}>Close</button>
    </div>
  ),
}));

describe("NotificationBell", () => {
  beforeEach(() => {
    mockApiGet.mockReset();
    mockApiGet.mockResolvedValue({ count: 0 });
    jest.useFakeTimers();
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it("renders bell button", async () => {
    render(<NotificationBell />);
    await act(async () => { await Promise.resolve(); });
    expect(screen.getByRole("button")).toBeInTheDocument();
  });

  it("does not show badge when unread count is zero", async () => {
    mockApiGet.mockResolvedValue({ count: 0 });
    render(<NotificationBell />);
    await act(async () => { await Promise.resolve(); });
    expect(screen.queryByText("0")).not.toBeInTheDocument();
  });

  it("shows badge with unread count when count > 0", async () => {
    mockApiGet.mockResolvedValue({ count: 5 });
    render(<NotificationBell />);
    await waitFor(() => {
      expect(screen.getByText("5")).toBeInTheDocument();
    });
  });

  it("caps badge at 99+ when count > 99", async () => {
    mockApiGet.mockResolvedValue({ count: 150 });
    render(<NotificationBell />);
    await waitFor(() => {
      expect(screen.getByText("99+")).toBeInTheDocument();
    });
  });

  it("opens dropdown on bell click", async () => {
    mockApiGet.mockResolvedValue({ count: 2 });
    render(<NotificationBell />);
    await waitFor(() => screen.getByText("2"));
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByTestId("notification-dropdown")).toBeInTheDocument();
  });

  it("closes dropdown when NotificationDropdown calls onClose", async () => {
    mockApiGet.mockResolvedValue({ count: 1 });
    render(<NotificationBell />);
    await waitFor(() => screen.getByText("1"));
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByTestId("notification-dropdown")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByTestId("notification-dropdown")).not.toBeInTheDocument();
  });

  it("polls /api/notifications/unread-count on mount", async () => {
    render(<NotificationBell />);
    await act(async () => { await Promise.resolve(); });
    expect(mockApiGet).toHaveBeenCalledWith("/api/notifications/unread-count", expect.objectContaining({ signal: expect.any(AbortSignal) }));
  });
});
