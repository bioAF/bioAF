import {
  STATUS_STYLES,
  statusStyle,
  statusBadgeClass,
  statusDotClass,
  statusLabel,
} from "@/lib/statusStyles";
import tailwindConfig from "../../tailwind.config.js";

describe("statusStyles library", () => {
  it("disambiguates the same status across entities", () => {
    // `running` is blue for a pipeline run but green for a compute session.
    expect(statusBadgeClass("pipelineRun", "running")).toBe("bg-blue-100 text-blue-700");
    expect(statusBadgeClass("computeSession", "running")).toBe("bg-green-100 text-green-800");
  });

  it("preserves experiment lifecycle labels and styles", () => {
    expect(statusLabel("experiment", "pipeline_complete")).toBe("Pipeline Complete");
    expect(statusBadgeClass("experiment", "pipeline_complete")).toBe("bg-teal-100 text-teal-800");
    expect(statusBadgeClass("experiment", "complete")).toBe("bg-green-100 text-green-800");
    expect(statusBadgeClass("experiment", "registered")).toBe("bg-gray-100 text-gray-800");
  });

  it("provides badge and dot variants for environment versions", () => {
    expect(statusBadgeClass("environmentVersion", "ready")).toBe("bg-green-100 text-green-700");
    expect(statusDotClass("environmentVersion", "ready")).toBe("bg-green-500");
    expect(statusLabel("environmentVersion", "building")).toBe("Building");
  });

  it("keeps reference dataset custom labels (uploading -> Importing)", () => {
    expect(statusLabel("referenceDataset", "uploading")).toBe("Importing");
    expect(statusBadgeClass("referenceDataset", "deprecated")).toBe("bg-red-100 text-red-800 line-through");
  });

  it("keeps SDR labels", () => {
    expect(statusLabel("sdr", "flagged_for_review")).toBe("Flagged for Review");
  });

  it("preserves the terraform run shade convention", () => {
    expect(statusBadgeClass("terraformRun", "applying")).toBe("text-blue-700 bg-blue-50");
  });

  it("covers orphaned resource cleanup statuses", () => {
    expect(statusBadgeClass("orphanedResource", "cleaned")).toBe("text-green-700 bg-green-50");
    expect(statusBadgeClass("orphanedResource", "detected")).toBe("text-amber-700 bg-amber-50");
  });

  it("falls back to neutral styling and humanized label for unknown status", () => {
    expect(statusBadgeClass("pipelineRun", "nonsense")).toBe("bg-gray-100 text-gray-600");
    expect(statusDotClass("environmentVersion", "nonsense")).toBe("bg-gray-400");
    expect(statusLabel("generic", "awaiting_confirmation")).toBe("awaiting confirmation");
  });

  it("falls back to neutral styling for an unknown entity", () => {
    expect(statusBadgeClass("madeUpEntity", "running")).toBe("bg-gray-100 text-gray-600");
  });

  it("statusStyle returns the full resolved entry", () => {
    const s = statusStyle("pipelineRun", "completed");
    expect(s.badge).toBe("bg-green-100 text-green-700");
    expect(s.label).toBe("completed");
  });

  it("exposes the registry for enumerations (e.g. dropdowns/legends)", () => {
    expect(Object.keys(STATUS_STYLES.sdr)).toContain("flagged_for_review");
  });
});

describe("tailwind content config covers statusStyles", () => {
  // statusStyles.ts is the single source of truth for status colors, but its
  // class names are plain string literals. Tailwind only emits CSS for classes
  // it finds in a content-globbed file; if the config does not scan src/lib,
  // colors used ONLY here (e.g. serviceHealth's bg-green-400 / bg-yellow-400 dots)
  // are purged from the built CSS and the dots render invisible. A unit test on
  // statusStyles' return values cannot catch this (the className is still set in
  // the DOM; only the CSS rule is missing), so guard the glob coverage directly.
  it("scans src/lib so statusStyles dot colors survive purge", () => {
    const content = tailwindConfig.content as string[];
    const coversLib = content.some((glob) => {
      const normalized = glob.replace(/^\.\//, "");
      return normalized.startsWith("src/**") || normalized.startsWith("src/lib");
    });
    expect(coversLib).toBe(true);
  });
});
