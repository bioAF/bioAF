import { render, screen, fireEvent } from "@testing-library/react";
import { SessionBucketFilter, SESSION_BUCKETS } from "@/components/shared/SessionBucketFilter";

describe("SessionBucketFilter", () => {
  it("exposes the three buckets in the order Active, Recent, All", () => {
    expect(SESSION_BUCKETS.map((b) => b.value)).toEqual(["active", "recent", "all"]);
    expect(SESSION_BUCKETS.map((b) => b.label)).toEqual(["Active", "Recent", "All"]);
  });

  it("renders three buttons and marks the value prop as selected", () => {
    render(<SessionBucketFilter value="recent" onChange={() => {}} />);
    expect(screen.getByRole("button", { name: /^Active$/ })).toBeInTheDocument();
    const recent = screen.getByRole("button", { name: /^Recent$/ });
    expect(recent).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /^All$/ })).toHaveAttribute("aria-pressed", "false");
  });

  it("calls onChange with the new bucket value when a button is clicked", () => {
    const onChange = jest.fn();
    render(<SessionBucketFilter value="active" onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: /^Recent$/ }));
    expect(onChange).toHaveBeenCalledWith("recent");
    fireEvent.click(screen.getByRole("button", { name: /^All$/ }));
    expect(onChange).toHaveBeenCalledWith("all");
  });

  it("does not call onChange when clicking the currently selected bucket", () => {
    const onChange = jest.fn();
    render(<SessionBucketFilter value="active" onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: /^Active$/ }));
    expect(onChange).not.toHaveBeenCalled();
  });
});
