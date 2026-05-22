import { render, screen, fireEvent } from "@testing-library/react";
import { NotificationItem } from "@/components/notifications/NotificationItem";

// NotificationItem now resolves a deep link and navigates on click, so it uses
// the router. These fixtures carry no entity reference, so they resolve to no
// link and clicking only marks them read (the behavior asserted below).
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
}));

const baseNotification = {
  id: 1,
  event_type: "pipeline_completed",
  title: "Pipeline finished",
  message: "Run #42 completed successfully",
  severity: "info",
  read: false,
  created_at: new Date(Date.now() - 5 * 60 * 1000).toISOString(), // 5 min ago
};

describe("NotificationItem", () => {
  it("renders title and message", () => {
    render(<NotificationItem notification={baseNotification} onMarkRead={jest.fn()} />);
    expect(screen.getByText("Pipeline finished")).toBeInTheDocument();
    expect(screen.getByText("Run #42 completed successfully")).toBeInTheDocument();
  });

  it("renders severity badge with info color", () => {
    render(<NotificationItem notification={baseNotification} onMarkRead={jest.fn()} />);
    expect(screen.getByText("info")).toBeInTheDocument();
  });

  it("renders warning severity badge", () => {
    render(
      <NotificationItem
        notification={{ ...baseNotification, severity: "warning" }}
        onMarkRead={jest.fn()}
      />
    );
    expect(screen.getByText("warning")).toBeInTheDocument();
  });

  it("renders critical severity badge", () => {
    render(
      <NotificationItem
        notification={{ ...baseNotification, severity: "critical" }}
        onMarkRead={jest.fn()}
      />
    );
    expect(screen.getByText("critical")).toBeInTheDocument();
  });

  it("shows bold title when unread", () => {
    render(<NotificationItem notification={{ ...baseNotification, read: false }} onMarkRead={jest.fn()} />);
    const title = screen.getByText("Pipeline finished");
    expect(title).toHaveClass("font-semibold");
  });

  it("does not show bold title when already read", () => {
    render(<NotificationItem notification={{ ...baseNotification, read: true }} onMarkRead={jest.fn()} />);
    const title = screen.getByText("Pipeline finished");
    expect(title).not.toHaveClass("font-semibold");
  });

  it("calls onMarkRead when clicking an unread notification", () => {
    const onMarkRead = jest.fn();
    render(<NotificationItem notification={{ ...baseNotification, read: false }} onMarkRead={onMarkRead} />);
    fireEvent.click(screen.getByText("Pipeline finished").closest("div")!);
    expect(onMarkRead).toHaveBeenCalled();
  });

  it("does not call onMarkRead when clicking an already-read notification", () => {
    const onMarkRead = jest.fn();
    render(<NotificationItem notification={{ ...baseNotification, read: true }} onMarkRead={onMarkRead} />);
    fireEvent.click(screen.getByText("Pipeline finished").closest("div")!);
    expect(onMarkRead).not.toHaveBeenCalled();
  });

  it("shows delete button when showActions and onDelete are provided", () => {
    render(
      <NotificationItem
        notification={baseNotification}
        onMarkRead={jest.fn()}
        showActions
        onDelete={jest.fn()}
      />
    );
    expect(screen.getByRole("button", { name: "Delete" })).toBeInTheDocument();
  });

  it("does not show delete button when showActions is false", () => {
    render(
      <NotificationItem
        notification={baseNotification}
        onMarkRead={jest.fn()}
        showActions={false}
        onDelete={jest.fn()}
      />
    );
    expect(screen.queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
  });

  it("calls onDelete and stops propagation when delete button clicked", () => {
    const onDelete = jest.fn();
    const onMarkRead = jest.fn();
    render(
      <NotificationItem
        notification={{ ...baseNotification, read: false }}
        onMarkRead={onMarkRead}
        showActions
        onDelete={onDelete}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(onDelete).toHaveBeenCalled();
    // onMarkRead should NOT be triggered (stopPropagation)
    expect(onMarkRead).not.toHaveBeenCalled();
  });

  it("shows relative time (e.g. 5m ago)", () => {
    render(<NotificationItem notification={baseNotification} onMarkRead={jest.fn()} />);
    expect(screen.getByText("5m ago")).toBeInTheDocument();
  });

  it("does not render message section when message is null", () => {
    render(
      <NotificationItem
        notification={{ ...baseNotification, message: null }}
        onMarkRead={jest.fn()}
      />
    );
    expect(screen.queryByText("Run #42 completed successfully")).not.toBeInTheDocument();
  });
});
