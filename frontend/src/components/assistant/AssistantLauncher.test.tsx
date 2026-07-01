import { render, screen, fireEvent } from "@testing-library/react";
import { AssistantLauncher } from "./AssistantLauncher";
import { assistantUiStore } from "./assistantUiStore";

jest.mock("@/hooks/usePermissions", () => ({ usePermissions: jest.fn() }));

import { usePermissions } from "@/hooks/usePermissions";

const mockUsePermissions = usePermissions as jest.Mock;

beforeEach(() => {
  mockUsePermissions.mockReset();
  assistantUiStore.close();
});

test("renders a launcher button for a permitted user", () => {
  mockUsePermissions.mockReturnValue({ canAccess: () => true, loading: false });
  render(<AssistantLauncher />);
  expect(screen.getByRole("button", { name: /assistant/i })).toBeInTheDocument();
});

test("renders nothing for a user without assistant:use", () => {
  mockUsePermissions.mockReturnValue({ canAccess: () => false, loading: false });
  render(<AssistantLauncher />);
  expect(screen.queryByRole("button", { name: /assistant/i })).not.toBeInTheDocument();
});

test("clicking toggles the shared open state", () => {
  mockUsePermissions.mockReturnValue({ canAccess: () => true, loading: false });
  render(<AssistantLauncher />);
  expect(assistantUiStore.getSnapshot()).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: /assistant/i }));
  expect(assistantUiStore.getSnapshot()).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: /assistant/i }));
  expect(assistantUiStore.getSnapshot()).toBe(false);
});
