/**
 * plan_8_3 stage 5: the study is held because the published sources do not establish which biological
 * unit each column came from. A person who knows (from the methods, a legend, the authors' code) records
 * it, with the evidence. The result is disclosed as assisted, and bioAF still refuses a unit that is only
 * what KIND of unit it is, so the server's refusal is shown rather than worked around.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { UnitConfirmation } from "./UnitConfirmation";

jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, roleName: "admin", loading: false }),
}));
jest.mock("@/lib/api", () => ({ api: { post: jest.fn() } }));

import { api } from "@/lib/api";

const mockPost = api.post as jest.Mock;

const OFFER = {
  matrix: "counts.txt.gz",
  columns: ["WT-1", "KO Cl16", "KO Cl33"],
  unresolved: ["KO Cl16", "KO Cl33"],
  recorded: null,
};

beforeEach(() => mockPost.mockReset());

test("asks only about the columns whose unit is not established", () => {
  render(<UnitConfirmation studyId={7} offer={OFFER} onChanged={jest.fn()} />);
  expect(screen.getByLabelText("KO Cl16")).toBeInTheDocument();
  expect(screen.getByLabelText("KO Cl33")).toBeInTheDocument();
  expect(screen.queryByLabelText("WT-1")).not.toBeInTheDocument();
});

test("records the units with the evidence they rest on", async () => {
  const onChanged = jest.fn();
  mockPost.mockResolvedValue({ id: 7 });
  render(<UnitConfirmation studyId={7} offer={OFFER} onChanged={onChanged} />);
  fireEvent.change(screen.getByLabelText("KO Cl16"), { target: { value: "clone Cl16" } });
  fireEvent.change(screen.getByLabelText("KO Cl33"), { target: { value: "clone Cl33" } });
  fireEvent.change(screen.getByLabelText("What establishes this"), {
    target: { value: "the methods name three independent clones" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Record these units" }));
  await waitFor(() => expect(onChanged).toHaveBeenCalledWith({ id: 7 }));
  expect(mockPost).toHaveBeenCalledWith("/api/validation-studies/7/unit-confirmations", {
    units: { "KO Cl16": "clone Cl16", "KO Cl33": "clone Cl33" },
    note: "the methods name three independent clones",
  });
});

test("will not send without the evidence", () => {
  render(<UnitConfirmation studyId={7} offer={OFFER} onChanged={jest.fn()} />);
  fireEvent.change(screen.getByLabelText("KO Cl16"), { target: { value: "clone Cl16" } });
  expect(screen.getByRole("button", { name: "Record these units" })).toBeDisabled();
});

test("shows the server's refusal in its own words", async () => {
  const refused = Object.assign(new Error('"knockout culture" is the kind of unit it is, not which culture it is'), {
    status: 422,
  });
  mockPost.mockRejectedValue(refused);
  render(<UnitConfirmation studyId={7} offer={OFFER} onChanged={jest.fn()} />);
  fireEvent.change(screen.getByLabelText("KO Cl16"), { target: { value: "knockout culture" } });
  fireEvent.change(screen.getByLabelText("What establishes this"), { target: { value: "the methods" } });
  fireEvent.click(screen.getByRole("button", { name: "Record these units" }));
  expect(await screen.findByText(/the kind of unit it is/)).toBeInTheDocument();
});

test("says what a person already recorded, and who", () => {
  render(
    <UnitConfirmation
      studyId={7}
      offer={{
        ...OFFER,
        unresolved: [],
        recorded: {
          units: { "KO Cl16": "clone Cl16" },
          note: "the methods",
          confirmed_by: "a person",
          at: "2026-09-17T00:00:00+00:00",
          superseded_count: 0,
        },
      }}
      onChanged={jest.fn()}
    />,
  );
  const recorded = screen.getByTestId("unit-confirmation-recorded");
  expect(recorded).toHaveTextContent("KO Cl16");
  expect(recorded).toHaveTextContent("clone Cl16");
  expect(recorded).toHaveTextContent("a person");
});
