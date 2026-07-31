import { render, screen, waitFor } from "@testing-library/react";
import ActivityFeedPage from "./page";

jest.mock("next/link", () => {
  return function MockLink({
    children,
    href,
    ...rest
  }: {
    children: React.ReactNode;
    href: string;
    [key: string]: unknown;
  }) {
    return (
      <a href={href} {...rest}>
        {children}
      </a>
    );
  };
});

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
}));

jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
}));

jest.mock("@/components/layout/Breadcrumb", () => ({ Breadcrumb: () => null }));

jest.mock("@/lib/api", () => ({
  api: {
    get: jest.fn(),
  },
}));

import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;

beforeEach(() => {
  mockGet.mockReset();
});

describe("ActivityFeedPage entity links", () => {
  test("a component event links to the Components screen, not a per-component detail page", async () => {
    mockGet.mockResolvedValue({
      events: [
        {
          id: 1,
          user_id: 1,
          user_email: "admin@test.com",
          event_type: "component.enable",
          entity_type: "component",
          entity_id: 5,
          summary: "Component enabled",
          severity: "info",
          created_at: "2026-06-09T00:00:00Z",
        },
      ],
      total: 1,
    });

    render(<ActivityFeedPage />);

    const link = await waitFor(() => screen.getByTestId("entity-link"));
    expect(link).toHaveAttribute("href", "/infrastructure/components");
  });
});
