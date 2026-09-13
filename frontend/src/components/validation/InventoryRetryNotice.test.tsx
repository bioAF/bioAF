/**
 * plan_8_1 section 2.1: after the inventory stage failed, a person can ask bioAF to group the committed
 * claims again. When the paper's text was pasted, bioAF did not keep it and asks for it again.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { InventoryRetryNotice } from "./InventoryRetryNotice";

jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, roleName: "admin", loading: false }),
}));
jest.mock("@/lib/api", () => ({ api: { post: jest.fn() } }));

import { api } from "@/lib/api";

const mockPost = api.post as jest.Mock;

beforeEach(() => mockPost.mockReset());

test("groups the claims again and hands the updated study back", async () => {
  const onChanged = jest.fn();
  mockPost.mockResolvedValue({ id: 7 });
  render(<InventoryRetryNotice studyId={7} onChanged={onChanged} />);
  fireEvent.click(screen.getByRole("button", { name: "Group the claims into findings again" }));
  await waitFor(() => expect(onChanged).toHaveBeenCalledWith({ id: 7 }));
  expect(mockPost).toHaveBeenCalledWith("/api/validation-studies/7/inventory/retry", {});
});

test("asks for the pasted text when bioAF did not keep it, then sends it", async () => {
  const onChanged = jest.fn();
  const asked = Object.assign(new Error("Paste the paper's text again: bioAF did not keep the text it read."), {
    status: 409,
  });
  mockPost.mockRejectedValueOnce(asked).mockResolvedValueOnce({ id: 7 });
  render(<InventoryRetryNotice studyId={7} onChanged={onChanged} />);
  fireEvent.click(screen.getByRole("button", { name: "Group the claims into findings again" }));
  const box = await screen.findByLabelText("The paper's text");
  expect(screen.getByText(/Paste the paper's text again/)).toBeInTheDocument();
  fireEvent.change(box, { target: { value: "the paper" } });
  fireEvent.click(screen.getByRole("button", { name: "Group the claims with this text" }));
  await waitFor(() => expect(onChanged).toHaveBeenCalled());
  expect(mockPost).toHaveBeenLastCalledWith("/api/validation-studies/7/inventory/retry", { full_text: "the paper" });
});
