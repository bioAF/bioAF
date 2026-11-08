/**
 * The shell's two maintenance presentations (plan 9): before the app has loaded, the splash says an
 * upgrade is being finished (not that the server is unreachable); once the user is working, a
 * banner above the page says so and the page itself stays on screen.
 */
import { render, screen } from "@testing-library/react";

jest.mock("next/navigation", () => ({ useRouter: () => ({ replace: jest.fn(), push: jest.fn() }) }));
jest.mock("@/lib/auth", () => ({ isAuthenticated: () => true }));
jest.mock("@/components/layout/Sidebar", () => ({ Sidebar: () => <nav /> }));
jest.mock("@/components/layout/Header", () => ({ Header: () => <header /> }));
jest.mock("@/hooks/usePermissions", () => ({ usePermissions: () => ({ loading: false }) }));
jest.mock("@/hooks/useComponents", () => ({ useComponents: () => ({ loading: false }) }));

let state: Record<string, unknown> = {};
jest.mock("@/hooks/useBackendReady", () => ({ useBackendReady: () => state }));

import AppLayout from "./layout";

const page = (
  <main>
    <textarea aria-label="Notes" defaultValue="unsaved notes" />
  </main>
);

test("at boot the splash explains the upgrade", () => {
  state = { ready: false, unreachable: false, maintenance: { reason: "upgrade_in_progress" }, retryNow: jest.fn() };
  render(<AppLayout>{page}</AppLayout>);
  expect(screen.getByTestId("app-loading")).toHaveTextContent(/finishing an upgrade/i);
  expect(screen.getByTestId("app-loading")).not.toHaveTextContent(/cannot be reached/i);
});

test("a failed upgrade at boot says it needs an administrator", () => {
  state = { ready: false, unreachable: false, maintenance: { reason: "upgrade_failed" }, retryNow: jest.fn() };
  render(<AppLayout>{page}</AppLayout>);
  expect(screen.getByTestId("app-loading")).toHaveTextContent(/administrator/i);
});

test("while working, a banner explains and the page with its unsaved input stays", () => {
  state = { ready: true, unreachable: false, maintenance: { reason: "upgrade_in_progress" }, retryNow: jest.fn() };
  render(<AppLayout>{page}</AppLayout>);
  expect(screen.getByRole("status", { name: /upgrade/i })).toHaveTextContent(/finishing an upgrade/i);
  expect(screen.getByLabelText("Notes")).toHaveValue("unsaved notes");
});

test("no banner when there is no maintenance", () => {
  state = { ready: true, unreachable: false, maintenance: null, retryNow: jest.fn() };
  render(<AppLayout>{page}</AppLayout>);
  expect(screen.queryByRole("status", { name: /upgrade/i })).toBeNull();
});
