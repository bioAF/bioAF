import { render, screen, fireEvent, waitFor } from "@testing-library/react";

const mockPost = jest.fn();
let mockToken = "";

jest.mock("next/link", () => {
  return function MockLink({ children, href }: { children: React.ReactNode; href: string }) {
    return <a href={href}>{children}</a>;
  };
});
jest.mock("next/navigation", () => ({
  useSearchParams: () => ({ get: (k: string) => (k === "token" ? mockToken : null) }),
}));
jest.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  }
  return { api: { post: (...a: unknown[]) => mockPost(...a) }, ApiError };
});

import AcceptInvitePage from "./page";
import { ApiError } from "@/lib/api";

const fill = (label: RegExp, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });
const submit = () => fireEvent.click(screen.getByRole("button", { name: "Accept invitation" }));

beforeEach(() => {
  mockPost.mockReset();
  mockToken = "tok";
  jest.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => jest.restoreAllMocks());

test("a link with no token says so and offers no form", () => {
  mockToken = "";
  render(<AcceptInvitePage />);

  expect(screen.getByText("Invitation link invalid")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Accept invitation" })).not.toBeInTheDocument();
});

test("accepting sends the token, password, and name, then points to sign in", async () => {
  mockPost.mockResolvedValue({ message: "ok" });
  render(<AcceptInvitePage />);

  fill(/^Name/, "Ada");
  fill(/^Password/, "chosenpassword1");
  fill(/^Confirm password/, "chosenpassword1");
  submit();

  await waitFor(() =>
    expect(mockPost).toHaveBeenCalledWith("/api/users/accept-invite", {
      token: "tok",
      password: "chosenpassword1",
      name: "Ada",
    }),
  );
  expect(await screen.findByText("Invitation accepted")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Go to sign in" })).toHaveAttribute("href", "/login");
});

test("the name is optional", async () => {
  mockPost.mockResolvedValue({});
  render(<AcceptInvitePage />);

  fill(/^Password/, "chosenpassword1");
  fill(/^Confirm password/, "chosenpassword1");
  submit();

  await waitFor(() =>
    expect(mockPost).toHaveBeenCalledWith("/api/users/accept-invite", { token: "tok", password: "chosenpassword1" }),
  );
});

test("a short password is refused before anything is sent", () => {
  render(<AcceptInvitePage />);
  fill(/^Password/, "short");
  fill(/^Confirm password/, "short");
  submit();

  expect(screen.getByText("Password must be at least 8 characters")).toBeInTheDocument();
  expect(mockPost).not.toHaveBeenCalled();
});

test("passwords that differ are refused before anything is sent", () => {
  render(<AcceptInvitePage />);
  fill(/^Password/, "chosenpassword1");
  fill(/^Confirm password/, "chosenpassword2");
  submit();

  expect(screen.getByText("Passwords do not match")).toBeInTheDocument();
  expect(mockPost).not.toHaveBeenCalled();
});

test("a dead invitation tells the user to ask for a new one", async () => {
  mockPost.mockRejectedValue(new ApiError(400, "Invalid or expired invitation"));
  render(<AcceptInvitePage />);
  fill(/^Password/, "chosenpassword1");
  fill(/^Confirm password/, "chosenpassword1");
  submit();

  expect(await screen.findByText("Invitation link invalid")).toBeInTheDocument();
  expect(screen.getByText(/Ask your administrator to resend/)).toBeInTheDocument();
});

test("an outage is reported plainly and leaves the form to retry", async () => {
  mockPost.mockRejectedValue(new Error("Backend unavailable"));
  render(<AcceptInvitePage />);
  fill(/^Password/, "chosenpassword1");
  fill(/^Confirm password/, "chosenpassword1");
  submit();

  expect(await screen.findByText(/could not be accepted right now/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Accept invitation" })).toBeInTheDocument();
  expect(screen.queryByText(/Backend unavailable/)).not.toBeInTheDocument();
});
