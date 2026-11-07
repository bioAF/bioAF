import { NOT_SET } from "@/lib/placeholders";
import { formatRecordedTime, recordedTimeValue } from "@/lib/recordedTime";

describe("formatRecordedTime", () => {
  it("renders the not-set placeholder when no time was recorded", () => {
    expect(formatRecordedTime(null)).toBe(NOT_SET);
    expect(formatRecordedTime(null, "date")).toBe(NOT_SET);
  });

  it("renders a recorded time exactly as the pages did before", () => {
    const iso = "2026-03-04T05:06:07Z";
    expect(formatRecordedTime(iso)).toBe(new Date(iso).toLocaleString());
    expect(formatRecordedTime(iso, "date")).toBe(new Date(iso).toLocaleDateString());
  });

  it("never shows the 1970 epoch for a missing time", () => {
    expect(formatRecordedTime(null)).not.toContain("1970");
  });
});

describe("recordedTimeValue", () => {
  it("sorts a record with no recorded time after every dated one, newest first", () => {
    const rows = [
      { id: 1, created_at: null },
      { id: 2, created_at: "2026-01-01T00:00:00Z" },
      { id: 3, created_at: "2026-02-01T00:00:00Z" },
    ];
    const sorted = [...rows].sort((a, b) => recordedTimeValue(b.created_at) - recordedTimeValue(a.created_at));
    expect(sorted.map((r) => r.id)).toEqual([3, 2, 1]);
  });
});
