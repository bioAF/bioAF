import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { InviteForm } from "@/components/auth/InviteForm";

const mockApiPost = jest.fn();
jest.mock("@/lib/api", () => ({
  api: { post: (...args: unknown[]) => mockApiPost(...args) },
}));

const mockRoles = [
  { id: 1, name: "admin", description: null, organization_id: 1, is_system: true, permissions: [], created_at: "" },
  { id: 2, name: "comp_bio", description: null, organization_id: 1, is_system: true, permissions: [], created_at: "" },
  { id: 3, name: "bench", description: null, organization_id: 1, is_system: true, permissions: [], created_at: "" },
  { id: 4, name: "viewer", description: null, organization_id: 1, is_system: true, permissions: [], created_at: "" },
];

describe("InviteForm", () => {
  beforeEach(() => {
    mockApiPost.mockReset();
  });

  it("renders email input, role selector, and invite button", () => {
    render(<InviteForm roles={mockRoles} />);
    expect(screen.getByPlaceholderText("Email address")).toBeInTheDocument();
    expect(screen.getByRole("combobox")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Invite" })).toBeInTheDocument();
  });

  it("renders all four role options", () => {
    render(<InviteForm roles={mockRoles} />);
    const select = screen.getByRole("combobox");
    expect(select).toContainHTML("admin");
    expect(select).toContainHTML("comp_bio");
    expect(select).toContainHTML("bench");
    expect(select).toContainHTML("viewer");
  });

  it("calls POST /api/users with email and selected role_id on invite", async () => {
    mockApiPost.mockResolvedValue({});
    render(<InviteForm roles={mockRoles} />);

    fireEvent.change(screen.getByPlaceholderText("Email address"), {
      target: { value: "alice@bioaf.org" },
    });
    fireEvent.change(screen.getByRole("combobox"), {
      target: { value: "bench" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Invite" }));

    await waitFor(() => {
      expect(mockApiPost).toHaveBeenCalledWith("/api/users", {
        email: "alice@bioaf.org",
        role_id: 3,
      });
    });
  });

  it("shows invited email in results list after successful invite", async () => {
    mockApiPost.mockResolvedValue({});
    render(<InviteForm roles={mockRoles} />);

    fireEvent.change(screen.getByPlaceholderText("Email address"), {
      target: { value: "alice@bioaf.org" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Invite" }));

    await waitFor(() => {
      expect(screen.getByText(/alice@bioaf.org/)).toBeInTheDocument();
      expect(screen.getByText(/invited/)).toBeInTheDocument();
    });
  });

  it("shows error message on invite failure", async () => {
    mockApiPost.mockRejectedValue(new Error("User already exists"));
    render(<InviteForm roles={mockRoles} />);

    fireEvent.change(screen.getByPlaceholderText("Email address"), {
      target: { value: "exists@bioaf.org" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Invite" }));

    await waitFor(() => {
      expect(screen.getByText("User already exists")).toBeInTheDocument();
    });
  });

  it("calls bulk invite endpoint with parsed emails", async () => {
    mockApiPost.mockResolvedValue({ results: [{ email: "a@b.com", status: "invited" }] });
    render(<InviteForm roles={mockRoles} />);

    fireEvent.change(screen.getByLabelText(/Bulk invite/i), {
      target: { value: "a@b.com\nb@c.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Bulk Invite" }));

    await waitFor(() => {
      expect(mockApiPost).toHaveBeenCalledWith(
        "/api/users/bulk-invite",
        expect.objectContaining({
          invites: expect.arrayContaining([
            { email: "a@b.com", role_id: 2 },
            { email: "b@c.com", role_id: 2 },
          ]),
        })
      );
    });
  });
});
