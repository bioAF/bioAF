"use client";

/**
 * How many rows a list shows at once.
 *
 * There was no per-page control anywhere in the app, and the hardcoded values
 * had already drifted apart: `page_size` appears in 35 files as 100 (x5), 50,
 * 25, 24 and 20. Adding a select per page would have spread that further, so
 * the options and the default are decided here, once, and every list that
 * offers the choice imports them.
 *
 * The bound that actually protects the database is on the endpoint, not here:
 * this control is not the only caller.
 */

/** Owner's choice, 2026-08-09: default 25, with 50 and 100. */
export const PAGE_SIZES = [25, 50, 100] as const;
export const DEFAULT_PAGE_SIZE = 25;

export type PageSize = (typeof PAGE_SIZES)[number];

interface Props {
  value: number;
  /**
   * Receives a number. Callers put this straight into `page_size` and then do
   * arithmetic on it for "showing X-Y of Z", where a string silently produces
   * "251" instead of 26.
   */
  onChange: (size: number) => void;
  className?: string;
}

export function PageSizeSelect({ value, onChange, className = "" }: Props) {
  return (
    <label className={`flex items-center gap-2 text-sm text-gray-500 ${className}`}>
      <span>Rows per page</span>
      <select
        aria-label="Rows per page"
        value={String(value)}
        onChange={(e) => onChange(Number(e.target.value))}
        className="border border-gray-300 rounded-md px-2 py-1 text-sm"
      >
        {PAGE_SIZES.map((size) => (
          <option key={size} value={String(size)}>
            {size}
          </option>
        ))}
      </select>
    </label>
  );
}
