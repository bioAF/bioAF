import { formatBytes } from "./bytes";
import { NOT_SET } from "./placeholders";

/*
 * Characterization: the eleven local formatters this helper replaced, copied
 * verbatim from 1915f118. For every valid size (zero, or a finite size of at
 * least one byte below the profile's overflow) the shared helper must print
 * exactly what the old code printed for its callers.
 */
const legacy = {
  reference(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
  },
  backup(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
    if (bytes < 1073741824) return `${(bytes / 1048576).toFixed(1)} MB`;
    return `${(bytes / 1073741824).toFixed(1)} GB`;
  },
  cellxgene(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
    if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
  },
  file(bytes: number): string {
    if (bytes === 0) return "0 B";
    const units = ["B", "KB", "MB", "GB", "TB"];
    const i = Math.floor(Math.log(bytes) / Math.log(1024));
    return `${(bytes / Math.pow(1024, i)).toFixed(1)} ${units[i]}`;
  },
  storage(bytes: number): string {
    if (bytes === 0) return "0 B";
    const units = ["B", "KB", "MB", "GB", "TB"];
    const i = Math.floor(Math.log(bytes) / Math.log(1024));
    const value = bytes / Math.pow(1024, i);
    return `${value.toFixed(value < 10 ? 2 : 1)} ${units[i]}`;
  },
};

const KB = 1024;
const MB = KB * 1024;
const GB = MB * 1024;
const TB = GB * 1024;

const GRID = [
  0, 1, 2, 9, 10, 99, 512, 999, 1000, 1023, 1023.9, 1024, 1025, 1536, 10 * KB - 1, 10 * KB, 51.2 * KB, 999 * KB,
  MB - 1, MB - 52, MB, MB + 1, 1.5 * MB, 10 * MB - 1, 10 * MB, 123456789, GB - 1, GB - 5 * MB, GB, GB + 1,
  1.25 * GB, 9.99 * GB, 10 * GB, 999 * GB, TB - 1, TB, 1.5 * TB, 9.999 * TB, 10 * TB, 1023 * TB,
];

describe.each(Object.keys(legacy) as (keyof typeof legacy)[])("%s profile keeps its existing output", (profile) => {
  test.each(GRID)("%p bytes", (bytes) => {
    // The GB-capped profiles kept growing in GB; the TB-capped ones overflow at 1024 TB.
    expect(formatBytes(bytes, profile, { missing: "x" })).toBe(legacy[profile](bytes));
  });
});

describe("named outputs", () => {
  test.each([
    ["reference", 0, "0 B"],
    ["reference", 512, "512 B"],
    ["reference", 1536, "1.5 KB"],
    ["reference", 2.5 * GB, "2.50 GB"],
    ["reference", 3 * TB, "3072.00 GB"],
    ["backup", 2.5 * GB, "2.5 GB"],
    ["cellxgene", 1536, "2 KB"],
    ["cellxgene", 1.5 * MB, "1.5 MB"],
    ["file", 512, "512.0 B"],
    ["file", 1.5 * TB, "1.5 TB"],
    ["storage", 512, "512.0 B"],
    ["storage", 5 * GB, "5.00 GB"],
    ["storage", 12 * GB, "12.0 GB"],
  ] as const)("%s %p -> %s", (profile, bytes, expected) => {
    expect(formatBytes(bytes, profile, { missing: "x" })).toBe(expected);
  });
});

describe("missing and zero", () => {
  test("null and undefined use the caller's missing wording", () => {
    expect(formatBytes(null, "reference", { missing: NOT_SET })).toBe(NOT_SET);
    expect(formatBytes(undefined, "cellxgene", { missing: "Unknown size" })).toBe("Unknown size");
    expect(formatBytes(null, "file", { missing: "-" })).toBe("-");
  });

  test("zero is a size, except where the caller treats it as missing (backup)", () => {
    expect(formatBytes(0, "reference", { missing: NOT_SET })).toBe("0 B");
    expect(formatBytes(0, "file", { missing: "-" })).toBe("0 B");
    expect(formatBytes(0, "storage", { missing: NOT_SET })).toBe("0 B");
    expect(formatBytes(0, "backup", { missing: "N/A", zeroIsMissing: true })).toBe("N/A");
    expect(formatBytes(null, "backup", { missing: "N/A", zeroIsMissing: true })).toBe("N/A");
  });
});

describe("corrected invalid and boundary output", () => {
  // The old log-based formatters printed "NaN undefined" for a negative size,
  // "0.5 undefined" for a fraction of a byte, and "1.0 undefined" at 1024 TB.
  test.each([-1, -2048, Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY])(
    "%p uses the missing wording in every profile",
    (bytes) => {
      for (const profile of Object.keys(legacy) as (keyof typeof legacy)[]) {
        expect(formatBytes(bytes, profile, { missing: "x" })).toBe("x");
      }
    },
  );

  test("a fraction of a byte stays in bytes", () => {
    expect(formatBytes(0.5, "file", { missing: "x" })).toBe("0.5 B");
    expect(formatBytes(0.5, "storage", { missing: "x" })).toBe("0.50 B");
    expect(formatBytes(0.5, "reference", { missing: "x" })).toBe("0.5 B");
  });

  test("sizes at or above 1024 TB stay in TB", () => {
    expect(formatBytes(1024 * TB, "file", { missing: "x" })).toBe("1024.0 TB");
    expect(formatBytes(5000 * TB, "storage", { missing: "x" })).toBe("5000.0 TB");
    expect(formatBytes(Number.MAX_SAFE_INTEGER, "file", { missing: "x" })).toMatch(/ TB$/);
  });
});
