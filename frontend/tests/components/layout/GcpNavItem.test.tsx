/**
 * Tests that "GCP Configuration" appears in the Settings nav for admin users
 * and does not appear for non-admin users.
 */
import { render, screen, fireEvent } from "@testing-library/react";
import { Sidebar } from "@/components/layout/Sidebar";

const mockPathname = jest.fn().mockReturnValue("/settings/gcp");
jest.mock("next/navigation", () => ({
  usePathname: () => mockPathname(),
}));

const mockGetCurrentUser = jest.fn();
jest.mock("@/lib/auth", () => ({
  getCurrentUser: () => mockGetCurrentUser(),
}));

describe("GCP Configuration nav item", () => {
  beforeEach(() => {
    mockPathname.mockReturnValue("/dashboard");
  });

  it("appears in Settings section for admin users", () => {
    mockGetCurrentUser.mockReturnValue({ email: "admin@bioaf.org", role: "admin", sub: "1" });
    render(<Sidebar />);

    // Expand Settings section
    fireEvent.click(screen.getByText("Settings"));
    expect(screen.getByText("GCP Configuration")).toBeInTheDocument();
  });

  it("links to /settings/gcp", () => {
    mockGetCurrentUser.mockReturnValue({ email: "admin@bioaf.org", role: "admin", sub: "1" });
    render(<Sidebar />);

    fireEvent.click(screen.getByText("Settings"));
    const link = screen.getByText("GCP Configuration").closest("a");
    expect(link).toHaveAttribute("href", "/settings/gcp");
  });

  it("does not appear for non-admin users (Settings section hidden)", () => {
    mockGetCurrentUser.mockReturnValue({ email: "bench@bioaf.org", role: "bench", sub: "2" });
    render(<Sidebar />);
    expect(screen.queryByText("GCP Configuration")).not.toBeInTheDocument();
  });

  it("is positioned between Audit Log and Naming Profiles", () => {
    mockGetCurrentUser.mockReturnValue({ email: "admin@bioaf.org", role: "admin", sub: "1" });
    render(<Sidebar />);

    fireEvent.click(screen.getByText("Settings"));

    const items = screen.getAllByRole("link").map((el) => el.textContent);
    const auditLogIdx = items.findIndex((t) => t === "Audit Log");
    const gcpIdx = items.findIndex((t) => t === "GCP Configuration");
    const namingIdx = items.findIndex((t) => t === "Naming Profiles");

    expect(auditLogIdx).toBeGreaterThanOrEqual(0);
    expect(gcpIdx).toBeGreaterThan(auditLogIdx);
    expect(namingIdx).toBeGreaterThan(gcpIdx);
  });
});
