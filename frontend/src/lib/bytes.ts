/**
 * The one byte-size formatter. Sizes use the 1024 scale with B/KB/MB/GB/TB
 * labels.
 *
 * Eleven screens each had their own copy, and they printed sizes differently.
 * Those differences are kept as named profiles so every screen still prints
 * exactly what it printed before (characterized in bytes.test.ts); choosing
 * one precision for the whole app is a separate product decision.
 *
 * - reference: references and reference upload. Bytes as supplied, KB/MB to
 *   one decimal, GB to two, never above GB.
 * - backup: one decimal for KB/MB/GB, never above GB.
 * - cellxgene: KB whole, MB one decimal, GB two, never above GB.
 * - file: file lists, exports, plots. One decimal in every unit up to TB.
 * - storage: component storage buckets. Two decimals below 10, else one, up to TB.
 *
 * What a missing size says belongs to the caller (`missing`). A negative or
 * non-finite size is not a size and uses the same wording. Sizes below one
 * byte stay in B and sizes past the largest unit stay in it; the old copies
 * printed "undefined" for both.
 */
export type ByteProfile = "reference" | "backup" | "cellxgene" | "file" | "storage";

export interface ByteFormat {
  /** Shown for null, undefined, negative and non-finite sizes. */
  missing: string;
  /** Backup has always shown a zero size as missing. */
  zeroIsMissing?: boolean;
}

const UNITS = ["B", "KB", "MB", "GB", "TB"];

interface Profile {
  maxUnit: number;
  /** Decimal places for a value in a unit; null prints the byte count as supplied. */
  decimals: (unit: number, value: number) => number | null;
}

const PROFILES: Record<ByteProfile, Profile> = {
  reference: { maxUnit: 3, decimals: (unit) => [null, 1, 1, 2][unit] },
  backup: { maxUnit: 3, decimals: (unit) => [null, 1, 1, 1][unit] },
  cellxgene: { maxUnit: 3, decimals: (unit) => [null, 0, 1, 2][unit] },
  file: { maxUnit: 4, decimals: () => 1 },
  storage: { maxUnit: 4, decimals: (_unit, value) => (value < 10 ? 2 : 1) },
};

export function formatBytes(bytes: number | null | undefined, profile: ByteProfile, format: ByteFormat): string {
  if (bytes == null || !Number.isFinite(bytes) || bytes < 0) return format.missing;
  if (bytes === 0) return format.zeroIsMissing ? format.missing : "0 B";

  const { maxUnit, decimals } = PROFILES[profile];
  let unit = 0;
  while (unit < maxUnit && bytes >= Math.pow(1024, unit + 1)) unit++;
  const value = bytes / Math.pow(1024, unit);
  const places = decimals(unit, value);
  return `${places === null ? value : value.toFixed(places)} ${UNITS[unit]}`;
}
