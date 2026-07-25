import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Level3Gate } from "./Level3Gate";

jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, roleName: "admin", loading: false, permissions: new Set() }),
}));

jest.mock("@/lib/api", () => ({
  api: { put: jest.fn(), post: jest.fn() },
}));

import { api } from "@/lib/api";

const mockPut = api.put as jest.Mock;
const mockPost = api.post as jest.Mock;

const DESIGN = {
  contrasts: [
    {
      name: "dex vs untreated",
      test_condition: "dex",
      reference_condition: "untreated",
      test_samples: ["SRX1", "SRX2"],
      reference_samples: ["SRX3", "SRX4"],
    },
  ],
  thresholds: { log2fc: 1.0, padj: 0.05 },
};

beforeEach(() => {
  mockPut.mockReset();
  mockPost.mockReset();
  mockPut.mockResolvedValue({ id: 1, state: "plan_ready" });
  mockPost.mockResolvedValue({ id: 1, state: "plan_ready" });
});

test("renders the extracted differential design for review", () => {
  render(<Level3Gate studyId={1} design={DESIGN} claim={null} onChanged={jest.fn()} />);
  expect(screen.getByDisplayValue("dex vs untreated")).toBeInTheDocument();
  expect(screen.getByDisplayValue("SRX1, SRX2")).toBeInTheDocument();
  expect(screen.getByDisplayValue("SRX3, SRX4")).toBeInTheDocument();
});

test("saving an edited design PUTs the normalized contrast to the design endpoint", async () => {
  const onChanged = jest.fn();
  render(<Level3Gate studyId={7} design={DESIGN} claim={null} onChanged={onChanged} />);

  const testInput = screen.getByLabelText(/test samples/i);
  await userEvent.clear(testInput);
  await userEvent.type(testInput, "SRX30659361, SRX30659364");
  await userEvent.click(screen.getByRole("button", { name: /save design/i }));

  await waitFor(() => expect(mockPut).toHaveBeenCalledTimes(1));
  const [url, body] = mockPut.mock.calls[0];
  expect(url).toBe("/api/validation-studies/7/differential-design");
  expect(body.contrasts[0].test_samples).toEqual(["SRX30659361", "SRX30659364"]);
  expect(body.contrasts[0].reference_samples).toEqual(["SRX3", "SRX4"]);
  expect(body.thresholds).toEqual({ log2fc: 1.0, padj: 0.05 });
  expect(onChanged).toHaveBeenCalled();
});

test("confirming a pasted ground-truth table POSTs to the finding-set endpoint", async () => {
  const onChanged = jest.fn();
  render(<Level3Gate studyId={7} design={DESIGN} claim={null} onChanged={onChanged} />);

  await userEvent.type(screen.getByLabelText(/result table/i), "gene,log2FoldChange,padj\nA1BG,2.5,0.001");
  await userEvent.type(screen.getByLabelText(/source/i), "Table S3");
  await userEvent.click(screen.getByRole("button", { name: /confirm ground-truth set/i }));

  await waitFor(() => expect(mockPost).toHaveBeenCalledTimes(1));
  const [url, body] = mockPost.mock.calls[0];
  expect(url).toBe("/api/validation-studies/7/finding-set");
  expect(body.kind).toBe("gene");
  expect(body.table_text).toContain("A1BG");
  expect(body.source_locator).toBe("Table S3");
  expect(onChanged).toHaveBeenCalled();
});

test("shows the parsed finding-set summary once a claim is confirmed", () => {
  const claim = {
    kind: "gene",
    namespace: "symbol",
    confirmed: true,
    source_locator: "Table S3",
    thresholds: { log2fc: 1.0, padj: 0.05 },
    finding_set: { n_sig: 10, n_up: 6, n_down: 4, namespace: "symbol", parse_notes: [], entities: [] },
  };
  render(<Level3Gate studyId={1} design={DESIGN} claim={claim} onChanged={jest.fn()} />);
  expect(screen.getByText(/10/)).toBeInTheDocument();
  expect(screen.getByText(/confirmed/i)).toBeInTheDocument();
});
