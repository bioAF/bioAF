import { render, screen, fireEvent } from "@testing-library/react";
import { NotificationItem } from "./NotificationItem";

const mockPush = jest.fn();
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
}));

function makeNotif(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    event_type: "pipeline.completed",
    title: "Pipeline 'scrnaseq' completed",
    message: "Run 42 finished",
    severity: "info",
    read: false,
    created_at: new Date().toISOString(),
    metadata_json: { entity_type: "pipeline_run", entity_id: 42 },
    ...overrides,
  };
}

describe("NotificationItem", () => {
  beforeEach(() => mockPush.mockClear());

  it("navigates to the associated item and marks read when clicked", () => {
    const onMarkRead = jest.fn();
    render(<NotificationItem notification={makeNotif()} onMarkRead={onMarkRead} />);
    fireEvent.click(screen.getByText("Pipeline 'scrnaseq' completed"));
    expect(onMarkRead).toHaveBeenCalled();
    expect(mockPush).toHaveBeenCalledWith("/results/qc-dashboards?run=42");
  });

  it("just marks read (no navigation) when there is no destination", () => {
    const onMarkRead = jest.fn();
    render(
      <NotificationItem
        notification={makeNotif({ event_type: "budget.threshold_80", metadata_json: {} })}
        onMarkRead={onMarkRead}
      />,
    );
    fireEvent.click(screen.getByText("Pipeline 'scrnaseq' completed"));
    expect(onMarkRead).toHaveBeenCalled();
    expect(mockPush).not.toHaveBeenCalled();
  });

  it("navigates without re-marking an already-read notification", () => {
    const onMarkRead = jest.fn();
    render(<NotificationItem notification={makeNotif({ read: true })} onMarkRead={onMarkRead} />);
    fireEvent.click(screen.getByText("Pipeline 'scrnaseq' completed"));
    expect(onMarkRead).not.toHaveBeenCalled();
    expect(mockPush).toHaveBeenCalledWith("/results/qc-dashboards?run=42");
  });

  it("runs onNavigate (e.g. closing the dropdown) before navigating", () => {
    const onNavigate = jest.fn();
    render(
      <NotificationItem notification={makeNotif()} onMarkRead={jest.fn()} onNavigate={onNavigate} />,
    );
    fireEvent.click(screen.getByText("Pipeline 'scrnaseq' completed"));
    expect(onNavigate).toHaveBeenCalled();
    expect(mockPush).toHaveBeenCalled();
  });

  it("does not navigate when the delete action is clicked", () => {
    const onDelete = jest.fn();
    render(
      <NotificationItem
        notification={makeNotif()}
        onMarkRead={jest.fn()}
        showActions
        onDelete={onDelete}
      />,
    );
    fireEvent.click(screen.getByText("Delete"));
    expect(onDelete).toHaveBeenCalled();
    expect(mockPush).not.toHaveBeenCalled();
  });
});
