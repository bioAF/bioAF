import { render, screen, fireEvent } from "@testing-library/react";
import { AssistantLaunchToggle } from "./AssistantLaunchToggle";

describe("AssistantLaunchToggle", () => {
  it("reflects state and lets an admin toggle it", () => {
    const onChange = jest.fn();
    render(<AssistantLaunchToggle enabled={false} canConfigure={true} onChange={onChange} />);
    const box = screen.getByRole("checkbox");
    expect(box).not.toBeChecked();
    expect(box).toBeEnabled();
    fireEvent.click(box);
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it("is read-only for a user without settings:configure", () => {
    const onChange = jest.fn();
    render(<AssistantLaunchToggle enabled={true} canConfigure={false} onChange={onChange} />);
    const box = screen.getByRole("checkbox");
    expect(box).toBeChecked();
    expect(box).toBeDisabled();
  });
});
