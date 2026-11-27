/** Plan 03.5A: the organization certificate upload is offered only to people who may configure infrastructure. */
import { render, screen } from "@/testing/renderWithProviders";

jest.mock("@/components/settings/NetworkingSettingsContent", () => ({ NetworkingSettingsContent: () => <div>networking</div> }));
const perms = { allowed: false };
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: (r: string, a: string) => perms.allowed && r === "infrastructure" && a === "configure", loading: false }),
}));
import NetworkingSettingsPage from "./page";

test.each([
  [true, true],
  [false, false],
])("configure permission %s shows the upload: %s", (allowed, shown) => {
  perms.allowed = allowed;
  render(<NetworkingSettingsPage />);
  expect(!!screen.queryByRole("button", { name: "Upload certificate" })).toBe(shown);
});
