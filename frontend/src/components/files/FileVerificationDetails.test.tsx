import { render, screen, fireEvent, within } from "@testing-library/react";
import { FileVerificationDetails } from "./FileVerificationDetails";
import type { FileVerification } from "@/lib/types/files";

const HEX = "0123456789abcdef0123456789abcdef";
const OTHER = "ffffffffffffffffffffffffffffffff";
const URI = "gs://bucket/experiments/1/a.fastq.gz";

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

const base = { externalId: null, readyAt: null, storageUri: URI, recordedChecksum: null };

test("shows the external ID, or the shared not-set dash", () => {
  const { rerender } = render(<FileVerificationDetails {...base} externalId="LIMS-007" verification={null} />);
  expect(screen.getByText("LIMS-007")).toBeInTheDocument();
  rerender(<FileVerificationDetails {...base} verification={null} />);
  expect(screen.getByLabelText("External ID not set")).toBeInTheDocument();
});

test("a file that is not ready says so in words", () => {
  render(<FileVerificationDetails {...base} verification={v({ status: "queued", expected_checksum: HEX })} />);
  expect(screen.getByTestId("ready-at")).toHaveTextContent("Not ready yet");
});

test("explains what a checksum is", () => {
  render(<FileVerificationDetails {...base} verification={null} />);
  expect(
    screen.getByText(
      "A checksum is a file fingerprint used to check that the stored file matches the supplied file.",
    ),
  ).toBeInTheDocument();
});

test("a file with no verification says so without inventing a status", () => {
  render(<FileVerificationDetails {...base} verification={null} />);
  expect(screen.getByText("No verification result recorded")).toBeInTheDocument();
});

test("shows the supplied and stored checksums together, each copyable", () => {
  const writeText = jest.fn().mockResolvedValue(undefined);
  Object.assign(navigator, { clipboard: { writeText } });
  render(
    <FileVerificationDetails
      {...base}
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
  expect(screen.getByText("Does not match supplied checksum")).toBeInTheDocument();
  expect(screen.getByText("Supplied checksum (MD5)")).toBeInTheDocument();
  expect(screen.getByText("Stored file checksum (MD5)")).toBeInTheDocument();
  expect(screen.getByText(HEX)).toBeInTheDocument();
  expect(screen.getByText(OTHER)).toBeInTheDocument();
  expect(screen.getByText("The stored file’s checksum does not match the supplied checksum.")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Copy supplied checksum" }));
  expect(writeText).toHaveBeenCalledWith(HEX);
  fireEvent.click(screen.getByRole("button", { name: "Copy stored file checksum" }));
  expect(writeText).toHaveBeenCalledWith(OTHER);
  expect(screen.queryByText(/Expected MD5|Observed MD5/)).not.toBeInTheDocument();
});

test("a stored checksum not computed yet is described in words", () => {
  render(<FileVerificationDetails {...base} verification={v({ status: "queued", expected_checksum: HEX })} />);
  expect(screen.getByTestId("stored-checksum")).toHaveTextContent("Not checked yet");
});

test("shows progress for work in progress", () => {
  render(
    <FileVerificationDetails
      {...base}
      verification={v({ status: "verifying", bytes_processed: 1048576, total_bytes: 4194304, expected_checksum: HEX })}
    />,
  );
  expect(screen.getByText("Checking file")).toBeInTheDocument();
  expect(screen.getByText("1.0 MB of 4.0 MB")).toBeInTheDocument();
});

test("a verified file shows when; how and where are technical details", () => {
  render(
    <FileVerificationDetails
      {...base}
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
  expect(screen.getByText("Matches supplied checksum")).toBeInTheDocument();
  expect(screen.getByTestId("verified-at")).toHaveTextContent("2026");
  expect(screen.getByTestId("ready-at")).toHaveTextContent("2026");
  const technical = screen.getByText("Technical details").closest("details") as HTMLElement;
  expect(technical).not.toHaveAttribute("open");
  expect(within(technical).getByText("Storage provider checksum")).toBeInTheDocument();
  expect(within(technical).getByText("Storage location")).toBeInTheDocument();
  expect(within(technical).getByText(URI)).toBeInTheDocument();
  expect(screen.queryByText("GCS URI")).not.toBeInTheDocument();
});

test("a checksum recorded before verification existed is shown, with no result claimed", () => {
  render(
    <FileVerificationDetails
      {...base}
      recordedChecksum={HEX}
      verification={v({ status: "unverified_legacy", observed_checksum: HEX })}
    />,
  );
  expect(screen.getByText("No verification result recorded")).toBeInTheDocument();
  expect(screen.getByText("Recorded checksum (MD5), not checked")).toBeInTheDocument();
  expect(screen.getAllByText(HEX)).toHaveLength(1);
});

test("a file with no verification still shows a checksum recorded for it", () => {
  render(<FileVerificationDetails {...base} recordedChecksum={HEX} verification={null} />);
  expect(screen.getByText("Recorded checksum (MD5), not checked")).toBeInTheDocument();
  expect(screen.getByText(HEX)).toBeInTheDocument();
});

test.each([
  ["failed", "storage_unavailable", true, "Storage could not be read to check the file."],
  ["failed", "storage_permission_denied", false, "bioAF is not allowed to read the stored file."],
])("a %s check (%s) explains itself and can be run again", (status, code, retryable, reason) => {
  const onRetry = jest.fn();
  render(
    <FileVerificationDetails
      {...base}
      canRetry
      onRetry={onRetry}
      verification={v({ status, error_code: code, retryable, expected_checksum: HEX })}
    />,
  );
  expect(screen.getByText("Could not check file")).toBeInTheDocument();
  expect(screen.getByText(reason)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Check file again" }));
  expect(onRetry).toHaveBeenCalled();
});

test("a scheduled automatic retry is described, with no button", () => {
  render(
    <FileVerificationDetails
      {...base}
      canRetry
      onRetry={jest.fn()}
      verification={v({ status: "queued", error_code: "storage_unavailable", retryable: true, expected_checksum: HEX })}
    />,
  );
  expect(screen.getByText("Waiting to check file")).toBeInTheDocument();
  expect(screen.getByText(/will be retried automatically/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Check file again" })).not.toBeInTheDocument();
});

test("no retry button without permission", () => {
  render(
    <FileVerificationDetails
      {...base}
      canRetry={false}
      onRetry={jest.fn()}
      verification={v({ status: "failed", error_code: "storage_unavailable", retryable: true })}
    />,
  );
  expect(screen.queryByRole("button", { name: "Check file again" })).not.toBeInTheDocument();
});

test.each([
  ["waiting_expectation", "Waiting for the supplied checksum"],
  ["not_required", "Not checked: no checksum was provided"],
  ["queued", "Waiting to check file"],
  ["preparing", "Preparing file for checking"],
  ["verifying", "Checking file"],
  ["verified", "Matches supplied checksum"],
])("status %s reads %s", (status, label) => {
  render(<FileVerificationDetails {...base} verification={v({ status })} />);
  expect(screen.getByText(label)).toBeInTheDocument();
});
