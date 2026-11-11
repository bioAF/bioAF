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

describe("formatRecordedTime display styles", () => {
  const UTC = { timeZone: "UTC" };
  const NEW_YORK = { timeZone: "America/New_York" };

  it("monthDayTime is the run list's compact US form, in the viewer's zone", () => {
    expect(formatRecordedTime("2026-03-11T14:05:09Z", "monthDayTime", UTC)).toBe("Mar 11 2:05 PM");
    expect(formatRecordedTime("2026-03-11T14:05:09Z", "monthDayTime", NEW_YORK)).toBe("Mar 11 10:05 AM");
  });

  it("fullWithSeconds is the run detail's US form with year and seconds", () => {
    expect(formatRecordedTime("2026-03-11T14:05:09Z", "fullWithSeconds", UTC)).toBe("Mar 11, 2026 2:05:09 PM");
  });

  it("an instant with a UTC offset is the same instant", () => {
    expect(formatRecordedTime("2026-03-11T10:05:09-04:00", "fullWithSeconds", UTC)).toBe("Mar 11, 2026 2:05:09 PM");
  });

  it("follows daylight saving in the viewer's zone", () => {
    // US clocks went forward at 2026-03-08 07:00 UTC.
    expect(formatRecordedTime("2026-03-08T06:59:00Z", "monthDayTime", NEW_YORK)).toBe("Mar 8 1:59 AM");
    expect(formatRecordedTime("2026-03-08T07:00:00Z", "monthDayTime", NEW_YORK)).toBe("Mar 8 3:00 AM");
  });

  it("an instant near midnight UTC lands on the viewer's own day", () => {
    expect(formatRecordedTime("2026-03-11T02:00:00Z", "fullWithSeconds", NEW_YORK)).toBe("Mar 10, 2026 10:00:00 PM");
  });

  it("without a zone, the browser's zone and locale are used, as before", () => {
    const iso = "2026-03-11T14:05:09Z";
    const d = new Date(iso);
    expect(formatRecordedTime(iso, "monthDayYearTime")).toBe(
      d.toLocaleString(undefined, { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" }),
    );
    expect(formatRecordedTime(iso, "monthDayTime")).toBe(
      d.toLocaleDateString("en-US", { month: "short", day: "numeric" }) + " " +
        d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" }),
    );
  });

  it("a caller can keep its own missing wording", () => {
    expect(formatRecordedTime(null, "datetime", { missing: "-" })).toBe("-");
    expect(formatRecordedTime(undefined, "monthDayTime")).toBe(NOT_SET);
  });

  it("an unreadable time is missing, not 'Invalid Date'", () => {
    for (const style of ["datetime", "date", "monthDayTime", "fullWithSeconds", "monthDayYearTime"] as const) {
      expect(formatRecordedTime("not a time", style)).toBe(NOT_SET);
    }
  });
});
