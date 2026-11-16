import { render, screen, fireEvent } from "@testing-library/react";
import { FileVerificationDetails } from "./FileVerificationDetails";
import type { FileVerification } from "@/lib/types/files";

const HEX = "0123456789abcdef0123456789abcdef";
const OTHER = "ffffffffffffffffffffffffffffffff";

const v = (overrides: Partial<FileVerification>): FileVerification => ({
  status: "queued",
  algorithm: "md5",
  expected_checksum: null,
  observed_checksum: null,
  method: null,
  verified_at: null,
  bytes_processed: null,
  total_bytes: null,
  error_code: null,
  retryable: null,
  ...overrides,
});

test("shows the external ID, or that none is assigned", () => {
  const { rerender } = render(
    <FileVerificationDetails
      externalId="LIMS-007"
      verification={null}
      readyAt={null}
    />,
  );
  expect(screen.getByText("LIMS-007")).toBeInTheDocument();
  rerender(
    <FileVerificationDetails
      externalId={null}
      verification={null}
      readyAt={null}
    />,
  );
  expect(screen.getByLabelText("External ID not set")).toBeInTheDocument();
});

test("a file with no verification says so without inventing a status", () => {
  render(
    <FileVerificationDetails
      externalId={null}
      verification={null}
      readyAt={null}
    />,
  );
  expect(
    screen.getByText("No checksum verification recorded"),
  ).toBeInTheDocument();
});

test("shows expected and observed checksums separately, each copyable", () => {
  const writeText = jest.fn().mockResolvedValue(undefined);
  Object.assign(navigator, { clipboard: { writeText } });
  render(
    <FileVerificationDetails
      externalId={null}
      readyAt={null}
      verification={v({
        status: "checksum_mismatch",
        expected_checksum: HEX,
        observed_checksum: OTHER,
        method: "streamed",
        error_code: "checksum_mismatch",
        retryable: false,
      })}
    />,
  );
  expect(screen.getByText("Checksum mismatch")).toBeInTheDocument();
  expect(screen.getByText(HEX)).toBeInTheDocument();
  expect(screen.getByText(OTHER)).toBeInTheDocument();
  expect(
    screen.getByText("The file's checksum does not match the expected value."),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Copy expected MD5" }));
  expect(writeText).toHaveBeenCalledWith(HEX);
  fireEvent.click(screen.getByRole("button", { name: "Copy observed MD5" }));
  expect(writeText).toHaveBeenCalledWith(OTHER);
});

test("shows progress for work in progress", () => {
  render(
    <FileVerificationDetails
      externalId={null}
      readyAt={null}
      verification={v({
        status: "verifying",
        bytes_processed: 1048576,
        total_bytes: 4194304,
        expected_checksum: HEX,
      })}
    />,
  );
  expect(screen.getByText("Verifying")).toBeInTheDocument();
  expect(screen.getByText("1.0 MB of 4.0 MB")).toBeInTheDocument();
});

test("a verified file shows how and when", () => {
  render(
    <FileVerificationDetails
      externalId={null}
      readyAt="2026-11-15T12:00:00Z"
      verification={v({
        status: "verified",
        expected_checksum: HEX,
        observed_checksum: HEX,
        method: "provider",
        verified_at: "2026-11-15T12:00:00Z",
      })}
    />,
  );
  expect(screen.getByText("Verified")).toBeInTheDocument();
  expect(screen.getByText("Storage provider checksum")).toBeInTheDocument();
  expect(screen.getByTestId("verified-at")).toHaveTextContent("2026");
  expect(screen.getByTestId("ready-at")).toHaveTextContent("2026");
});

test("legacy checksums are labelled as unverified", () => {
  render(
    <FileVerificationDetails
      externalId={null}
      readyAt={null}
      verification={v({
        status: "unverified_legacy",
        observed_checksum: HEX,
        algorithm: "md5",
      })}
    />,
  );
  expect(
    screen.getByText("Not verified (recorded before verification)"),
  ).toBeInTheDocument();
});

test.each([
  [
    "failed",
    "storage_unavailable",
    true,
    "Storage could not be read to verify the file.",
  ],
  [
    "failed",
    "storage_permission_denied",
    false,
    "bioAF is not allowed to read the stored file.",
  ],
])(
  "a %s verification (%s) explains itself and offers a retry",
  (status, code, retryable, reason) => {
    const onRetry = jest.fn();
    render(
      <FileVerificationDetails
        externalId={null}
        readyAt={null}
        canRetry
        onRetry={onRetry}
        verification={v({
          status,
          error_code: code,
          retryable,
          expected_checksum: HEX,
        })}
      />,
    );
    expect(screen.getByText("Verification failed")).toBeInTheDocument();
    expect(screen.getByText(reason)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry verification" }));
    expect(onRetry).toHaveBeenCalled();
  },
);

test("a scheduled automatic retry is described, with no retry button", () => {
  render(
    <FileVerificationDetails
      externalId={null}
      readyAt={null}
      canRetry
      onRetry={jest.fn()}
      verification={v({
        status: "queued",
        error_code: "storage_unavailable",
        retryable: true,
        expected_checksum: HEX,
      })}
    />,
  );
  expect(screen.getByText("Queued for verification")).toBeInTheDocument();
  expect(screen.getByText(/will be retried automatically/)).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Retry verification" }),
  ).not.toBeInTheDocument();
});

test("no retry button without permission", () => {
  render(
    <FileVerificationDetails
      externalId={null}
      readyAt={null}
      canRetry={false}
      onRetry={jest.fn()}
      verification={v({
        status: "failed",
        error_code: "storage_unavailable",
        retryable: true,
      })}
    />,
  );
  expect(
    screen.queryByRole("button", { name: "Retry verification" }),
  ).not.toBeInTheDocument();
});

test.each([
  ["waiting_expectation", "Waiting for expected checksum"],
  ["not_required", "No checksum to compare"],
  ["preparing", "Preparing a protected copy"],
])("status %s has a plain label", (status, label) => {
  render(
    <FileVerificationDetails
      externalId={null}
      readyAt={null}
      verification={v({ status })}
    />,
  );
  expect(screen.getByText(label)).toBeInTheDocument();
});
