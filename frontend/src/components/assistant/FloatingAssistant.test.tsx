import { render, screen, fireEvent } from "@testing-library/react";
import { FloatingAssistant } from "./FloatingAssistant";

let pathname = "/dashboard";
jest.mock("next/navigation", () => ({ usePathname: () => pathname }));

jest.mock("@/lib/auth", () => ({ isAuthenticated: jest.fn() }));
jest.mock("@/hooks/usePermissions", () => ({ usePermissions: jest.fn() }));
// The chat body is exercised in AssistantChat.test.tsx; stub it here so this suite tests only the
// bubble shell (visibility, open/close).
jest.mock("@/components/assistant/AssistantChat", () => ({
  AssistantChat: () => <div data-testid="assistant-chat-body" />,
}));

import { isAuthenticated } from "@/lib/auth";
import { usePermissions } from "@/hooks/usePermissions";

const mockIsAuthenticated = isAuthenticated as jest.Mock;
const mockUsePermissions = usePermissions as jest.Mock;

function eligible() {
  pathname = "/dashboard";
  mockIsAuthenticated.mockReturnValue(true);
  mockUsePermissions.mockReturnValue({ canAccess: () => true, loading: false });
}

beforeEach(() => {
  mockIsAuthenticated.mockReset();
  mockUsePermissions.mockReset();
  pathname = "/dashboard";
});

describe("FloatingAssistant", () => {
  it("renders the bubble for an authenticated, permitted user", () => {
    eligible();
    render(<FloatingAssistant />);
    expect(screen.getByRole("button", { name: /open assistant/i })).toBeInTheDocument();
    // The chat body is not mounted until the bubble is opened.
    expect(screen.queryByTestId("assistant-chat-body")).not.toBeInTheDocument();
  });

  it("hides on the login screen", () => {
    mockIsAuthenticated.mockReturnValue(true);
    mockUsePermissions.mockReturnValue({ canAccess: () => true, loading: false });
    pathname = "/login";
    render(<FloatingAssistant />);
    expect(screen.queryByRole("button", { name: /assistant/i })).not.toBeInTheDocument();
  });

  it("renders nothing when the user lacks assistant:use", () => {
    mockIsAuthenticated.mockReturnValue(true);
    mockUsePermissions.mockReturnValue({ canAccess: () => false, loading: false });
    render(<FloatingAssistant />);
    expect(screen.queryByRole("button", { name: /assistant/i })).not.toBeInTheDocument();
  });

  it("renders nothing when unauthenticated", () => {
    mockIsAuthenticated.mockReturnValue(false);
    mockUsePermissions.mockReturnValue({ canAccess: () => true, loading: false });
    render(<FloatingAssistant />);
    expect(screen.queryByRole("button", { name: /assistant/i })).not.toBeInTheDocument();
  });

  it("opens the chat panel on click and keeps it mounted after closing (session persists)", () => {
    eligible();
    render(<FloatingAssistant />);

    fireEvent.click(screen.getByRole("button", { name: /open assistant/i }));
    expect(screen.getByTestId("assistant-chat-body")).toBeInTheDocument();
    // classList.contains checks the exact "hidden" token (not the substring in "overflow-hidden").
    expect(screen.getByTestId("assistant-panel").classList.contains("hidden")).toBe(false);

    // Closing hides the panel but does NOT unmount the chat (transcript/conversation survive).
    fireEvent.click(screen.getByRole("button", { name: /minimize assistant/i }));
    expect(screen.getByTestId("assistant-chat-body")).toBeInTheDocument();
    expect(screen.getByTestId("assistant-panel").classList.contains("hidden")).toBe(true);
  });
});
