"use client";

import { useState } from "react";

import { api } from "@/lib/api";
import type {
  NamingProfile,
  NamingProfileDelimiter,
  NamingProfileTestResult,
  SegmentDateFormat,
  SegmentDefinition,
} from "@/lib/types";

interface Props {
  profile: NamingProfile;
  onClose: () => void;
  onEdit: () => void;
}

export function NamingProfileDetail({ profile, onClose, onEdit }: Props) {
  const [testFilename, setTestFilename] = useState("");
  const [testResult, setTestResult] = useState<NamingProfileTestResult | null>(null);
  const [error, setError] = useState("");

  async function handleTest() {
    if (!testFilename.trim()) return;
    setError("");
    try {
      const results = await api.post<NamingProfileTestResult[]>(
        "/api/naming-profiles/test",
        {
          filenames: [testFilename.trim()],
          delimiter: profile.delimiter,
          strip_extension: profile.strip_extension,
          segments: profile.segments,
        },
      );
      setTestResult(results[0] ?? null);
    } catch {
      setError("Test parse failed.");
    }
  }

  const example = buildExampleFilename(profile);

  return (
    <div
      role="dialog"
      aria-label={`Naming profile ${profile.name}`}
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
    >
      <div className="bg-white rounded-lg shadow-lg w-full max-w-3xl p-6 space-y-5 max-h-[90vh] overflow-y-auto">
        <div className="flex items-start justify-between">
          <div>
            <h3 className="text-lg font-semibold text-gray-900">{profile.name}</h3>
            {profile.description && (
              <p className="text-sm text-gray-500">{profile.description}</p>
            )}
          </div>
          <button
            type="button"
            aria-label="close detail"
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600"
          >
            ✕
          </button>
        </div>

        <section className="grid grid-cols-2 gap-4 text-sm">
          <div>
            <div className="text-xs uppercase text-gray-400">Delimiter</div>
            <div className="font-mono">{profile.delimiter}</div>
          </div>
          <div>
            <div className="text-xs uppercase text-gray-400">Strip extension</div>
            <div>{profile.strip_extension ? "Yes" : "No"}</div>
          </div>
          <div>
            <div className="text-xs uppercase text-gray-400">Status</div>
            <div>{profile.status}</div>
          </div>
          <div>
            <div className="text-xs uppercase text-gray-400">Template</div>
            <div>
              {profile.experiment_template_id == null
                ? "(none)"
                : `#${profile.experiment_template_id}`}
            </div>
          </div>
        </section>

        <section>
          <div className="text-xs uppercase text-gray-400 mb-1">Segments</div>
          <ul className="space-y-1 text-sm">
            {profile.segments.map((s, idx) => (
              <li key={`${s.field_name}-${idx}`} className="font-mono">
                {idx + 1}. {describeSegment(s, profile.delimiter)}
              </li>
            ))}
          </ul>
        </section>

        <section>
          <div className="text-xs uppercase text-gray-400 mb-1">Example filename</div>
          <code
            data-testid="example-filename"
            className="block bg-gray-50 border rounded px-3 py-2 font-mono text-sm break-all"
          >
            {example}
          </code>
        </section>

        <section>
          <label
            htmlFor="np-detail-test"
            className="block text-sm font-medium text-gray-700 mb-1"
          >
            Test against a real filename
          </label>
          <div className="flex gap-2">
            <input
              id="np-detail-test"
              value={testFilename}
              onChange={(e) => setTestFilename(e.target.value)}
              placeholder="Paste a real filename..."
              className="flex-1 border rounded-lg px-3 py-2 font-mono text-sm"
            />
            <button
              type="button"
              onClick={handleTest}
              className="px-4 py-2 bg-bioaf-600 text-white rounded-lg hover:bg-bioaf-700"
            >
              Parse
            </button>
          </div>
          {testResult && (
            <div
              data-testid="detail-parse-result"
              className="mt-3 border rounded-lg p-3 bg-gray-50 text-sm"
            >
              <div className="font-medium mb-1">Parsed</div>
              {Object.keys(testResult.parsed).length === 0 ? (
                <div className="italic text-gray-500">No fields recognized.</div>
              ) : (
                <ul className="space-y-1">
                  {Object.entries(testResult.parsed).map(([k, v]) => (
                    <li key={k} className="font-mono text-xs">
                      <span className="font-semibold">{k}</span>: {v}
                    </li>
                  ))}
                </ul>
              )}
              {testResult.unrecognized.length > 0 && (
                <>
                  <div className="font-medium mt-2 mb-1">Unrecognized</div>
                  <ul className="space-y-1">
                    {testResult.unrecognized.map((t) => (
                      <li key={t} className="font-mono text-xs text-gray-600">
                        {t}
                      </li>
                    ))}
                  </ul>
                </>
              )}
              {testResult.warnings.length > 0 && (
                <>
                  <div className="font-medium mt-2 mb-1">Warnings</div>
                  <ul className="space-y-1 text-amber-700 text-xs">
                    {testResult.warnings.map((w) => (
                      <li key={w}>{w}</li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          )}
          {error && (
            <div className="mt-2 text-sm text-red-700 bg-red-50 border border-red-200 rounded p-2">
              {error}
            </div>
          )}
        </section>

        <div className="flex gap-2 pt-2 border-t">
          <button
            type="button"
            onClick={onEdit}
            className="px-4 py-2 bg-bioaf-600 text-white rounded-lg hover:bg-bioaf-700"
          >
            Edit profile
          </button>
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function innerSeparator(delimiter: NamingProfileDelimiter): "_" | "-" {
  return delimiter === "_" ? "-" : "_";
}

function describeSegment(s: SegmentDefinition, delimiter: NamingProfileDelimiter): string {
  if (s.field_type === "date") {
    return `${s.field_name} (date, ${s.date_format ?? "?"})`;
  }
  if (s.field_type === "number") {
    return `${s.field_name} (${s.identifier ?? "?"}, number, padding ${s.padding ?? "-"})`;
  }
  return `${s.field_name} (${s.identifier ?? "?"}${innerSeparator(delimiter)}value, string)`;
}

function buildExampleFilename(profile: NamingProfile): string {
  const inner = innerSeparator(profile.delimiter);
  const parts = profile.segments.map((s) => exampleSegmentValue(s, inner));
  const joined = parts.join(profile.delimiter);
  return profile.strip_extension ? `${joined}.fastq.gz` : joined;
}

function exampleSegmentValue(s: SegmentDefinition, inner: "_" | "-"): string {
  if (s.field_type === "date") {
    return exampleDateForFormat(s.date_format ?? "YYYYMMDD");
  }
  const identifier = s.identifier ?? "X";
  if (s.field_type === "number") {
    const width = Math.max(1, s.padding ?? 0);
    return `${identifier}${"1".padStart(width, "0")}`;
  }
  return `${identifier}${inner}value`;
}

function exampleDateForFormat(format: SegmentDateFormat): string {
  if (format === "YYYYMMDD") return "20260602";
  if (format === "YYYY-MM-DD") return "2026-06-02";
  return "260602";
}
