/**
 * Behavioral tests for the Naming Profile detail modal.
 *
 * The modal is what shows when a user clicks a saved profile in the
 * settings list: a read-only summary, an example filename generated from
 * the profile, a live parser test, and an Edit button.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { NamingProfileDetail } from "./NamingProfileDetail";
import type { NamingProfile } from "@/lib/types";

jest.mock("@/lib/api", () => ({
  api: {
    post: jest.fn(),
  },
}));

import { api } from "@/lib/api";

const mockPost = api.post as jest.Mock;

beforeEach(() => {
  mockPost.mockReset();
});

const SAMPLE_PROFILE: NamingProfile = {
  id: 42,
  organization_id: 1,
  name: "Team A profile",
  description: "Filename convention for Team A",
  delimiter: "_",
  strip_extension: true,
  segments: [
    {
      position: 0,
      identifier: "SMP",
      field_name: "SampleID",
      field_type: "number",
      padding: 4,
      date_format: null,
      is_system_chip: true,
    },
    {
      position: 1,
      identifier: "req",
      field_name: "Requestor",
      field_type: "string",
      padding: null,
      date_format: null,
      is_system_chip: false,
    },
    {
      position: 2,
      identifier: null,
      field_name: "RunDate",
      field_type: "date",
      padding: null,
      date_format: "YYYYMMDD",
      is_system_chip: false,
    },
  ],
  experiment_template_id: null,
  status: "active",
  created_by: 1,
  created_at: "2026-06-02T00:00:00Z",
  updated_at: "2026-06-02T00:00:00Z",
};

test("renders profile metadata", () => {
  render(
    <NamingProfileDetail
      profile={SAMPLE_PROFILE}
      onClose={jest.fn()}
      onEdit={jest.fn()}
    />,
  );
  expect(screen.getByText("Team A profile")).toBeInTheDocument();
  expect(screen.getByText(/Filename convention for Team A/)).toBeInTheDocument();
  // Three segments described
  expect(screen.getByText(/SampleID.*number/i)).toBeInTheDocument();
  expect(screen.getByText(/Requestor.*string/i)).toBeInTheDocument();
  expect(screen.getByText(/RunDate.*date.*YYYYMMDD/i)).toBeInTheDocument();
});

test("renders an example filename built from the profile", () => {
  render(
    <NamingProfileDetail
      profile={SAMPLE_PROFILE}
      onClose={jest.fn()}
      onEdit={jest.fn()}
    />,
  );
  const example = screen.getByTestId("example-filename");
  // delimiter "_", padding 4 on SMP -> SMP0001;
  // inner sep is "-" for string -> req-value;
  // date format YYYYMMDD -> 20260602;
  // strip_extension=true -> append .fastq.gz
  expect(example).toHaveTextContent("SMP0001_req-value_20260602.fastq.gz");
});

test("test field calls backend with the saved profile shape and renders parsed map", async () => {
  const user = userEvent.setup();
  mockPost.mockResolvedValue([
    {
      filename: "SMP0042_req-bmills_20260603.fastq.gz",
      parsed: { SampleID: "0042", Requestor: "bmills", RunDate: "2026-06-03" },
      unrecognized: [],
      warnings: [],
    },
  ]);

  render(
    <NamingProfileDetail
      profile={SAMPLE_PROFILE}
      onClose={jest.fn()}
      onEdit={jest.fn()}
    />,
  );

  await user.type(
    screen.getByLabelText(/test against a real filename/i),
    "SMP0042_req-bmills_20260603.fastq.gz",
  );
  await user.click(screen.getByRole("button", { name: /parse$/i }));

  await waitFor(() => expect(mockPost).toHaveBeenCalled());
  const [url, payload] = mockPost.mock.calls[0];
  expect(url).toBe("/api/naming-profiles/test");
  expect(payload.delimiter).toBe("_");
  expect(payload.segments).toHaveLength(3);
  expect(payload.filenames).toEqual(["SMP0042_req-bmills_20260603.fastq.gz"]);

  const result = await screen.findByTestId("detail-parse-result");
  expect(within(result).getByText("SampleID")).toBeInTheDocument();
  expect(within(result).getByText(/0042/)).toBeInTheDocument();
  expect(within(result).getByText("Requestor")).toBeInTheDocument();
  expect(within(result).getByText(/bmills/)).toBeInTheDocument();
});

test("Edit button calls onEdit", async () => {
  const user = userEvent.setup();
  const onEdit = jest.fn();
  render(
    <NamingProfileDetail
      profile={SAMPLE_PROFILE}
      onClose={jest.fn()}
      onEdit={onEdit}
    />,
  );
  await user.click(screen.getByRole("button", { name: /edit profile/i }));
  expect(onEdit).toHaveBeenCalled();
});

test("every Close affordance calls onClose", async () => {
  // On the shared Modal shell there are two: the footer button this dialog
  // always had, and the shell's own named header close. The hand-rolled
  // header close was labelled "close detail"; neither was removed.
  const user = userEvent.setup();
  const onClose = jest.fn();
  render(
    <NamingProfileDetail
      profile={SAMPLE_PROFILE}
      onClose={onClose}
      onEdit={jest.fn()}
    />,
  );
  const closers = screen.getAllByRole("button", { name: /^close$/i });
  expect(closers).toHaveLength(2);
  for (const [i, button] of closers.entries()) {
    await user.click(button);
    expect(onClose).toHaveBeenCalledTimes(i + 1);
  }
});
