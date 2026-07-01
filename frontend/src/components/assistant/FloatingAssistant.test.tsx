import { render, screen, act } from "@testing-library/react";
import { FloatingAssistant } from "./FloatingAssistant";
import { assistantUiStore } from "./assistantUiStore";

let pathname = "/dashboard";
jest.mock("next/navigation", () => ({ usePathname: () => pathname }));

jest.mock("@/lib/auth", () => ({ isAuthenticated: jest.fn() }));
jest.mock("@/hooks/usePermissions", () => ({ usePermissions: jest.fn() }));
// The chat body is exercised in AssistantChat.test.tsx; stub it here so this suite tests only the
// panel host (visibility, open/close, gating).
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
  assistantUiStore.close();
});

describe("FloatingAssistant panel host", () => {
  it("mounts no panel until the assistant is opened (nothing floats to obscure content)", () => {
    eligible();
    render(<FloatingAssistant />);
    expect(screen.queryByTestId("assistant-panel")).not.toBeInTheDocument();
    expect(screen.queryByTestId("assistant-chat-body")).not.toBeInTheDocument();
  });

  it("shows the panel and mounts the chat when the shared store opens", () => {
    eligible();
    render(<FloatingAssistant />);

    act(() => assistantUiStore.open());
    expect(screen.getByTestId("assistant-panel")).toBeInTheDocument();
    expect(screen.getByTestId("assistant-chat-body")).toBeInTheDocument();
    expect(screen.getByTestId("assistant-panel").classList.contains("hidden")).toBe(false);
  });

  it("hides the panel on close but keeps the chat mounted (session persists)", () => {
    eligible();
    render(<FloatingAssistant />);

    act(() => assistantUiStore.open());
    act(() => assistantUiStore.close());
    // Still mounted (transcript/conversation survive), just hidden.
    expect(screen.getByTestId("assistant-chat-body")).toBeInTheDocument();
    expect(screen.getByTestId("assistant-panel").classList.contains("hidden")).toBe(true);
  });

  it("renders nothing on the login screen", () => {
    mockIsAuthenticated.mockReturnValue(true);
    mockUsePermissions.mockReturnValue({ canAccess: () => true, loading: false });
    pathname = "/login";
    render(<FloatingAssistant />);
    act(() => assistantUiStore.open());
    expect(screen.queryByTestId("assistant-panel")).not.toBeInTheDocument();
  });

  it("renders nothing when the user lacks assistant:use", () => {
    mockIsAuthenticated.mockReturnValue(true);
    mockUsePermissions.mockReturnValue({ canAccess: () => false, loading: false });
    render(<FloatingAssistant />);
    act(() => assistantUiStore.open());
    expect(screen.queryByTestId("assistant-panel")).not.toBeInTheDocument();
  });

  it("renders nothing when unauthenticated", () => {
    mockIsAuthenticated.mockReturnValue(false);
    mockUsePermissions.mockReturnValue({ canAccess: () => true, loading: false });
    render(<FloatingAssistant />);
    act(() => assistantUiStore.open());
    expect(screen.queryByTestId("assistant-panel")).not.toBeInTheDocument();
  });
});
