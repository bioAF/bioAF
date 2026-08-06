import { render as rtlRender, type RenderOptions } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { ConfirmProvider } from "@/hooks/useConfirm";

/**
 * Test-only render that mounts the app-level providers a component may reach
 * for. Not imported by any application code, so it is never bundled.
 *
 * `useConfirm` throws outside its provider on purpose (a no-op fallback would
 * let a destructive action run ungated), which means any test rendering a page
 * with a confirmation must supply it. Wrapping here rather than editing every
 * `render(...)` call keeps those test diffs to the import line, so the
 * assertions that prove behaviour did not change stay visibly untouched.
 *
 * ConfirmProvider renders nothing until a confirm is requested, so wrapping is
 * inert for tests that never trigger one.
 */
export function renderWithProviders(ui: ReactElement, options?: Omit<RenderOptions, "wrapper">) {
  return rtlRender(ui, {
    wrapper: ({ children }: { children: ReactNode }) => <ConfirmProvider>{children}</ConfirmProvider>,
    ...options,
  });
}

export * from "@testing-library/react";
// Deliberately shadows the re-exported RTL render above.
export { renderWithProviders as render };
